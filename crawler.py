import json
import os.path
import pickle
import re
from sys import exit

import requests
from requests.cookies import RequestsCookieJar
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager

from database import Problem, ProblemTag, Tag, Submission, create_tables, Solution
from utils import destructure, random_wait, do, get

COOKIE_PATH = "./cookies.dat"


class LeetCodeCrawler:
    def __init__(self):
        # create an http session
        self.session = requests.Session()
        
        # Configure Chrome options to suppress warnings and improve performance
        chrome_options = Options()
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-logging")
        chrome_options.add_argument("--disable-web-security")
        chrome_options.add_argument("--disable-features=VizDisplayCompositor")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-logging"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        chrome_options.add_argument("--log-level=3")  # Suppress INFO, WARNING, ERROR
        
        service = Service(ChromeDriverManager().install())
        self.browser = webdriver.Chrome(service=service, options=chrome_options)
        self.session.headers.update(
            {
                'Host': 'leetcode.com',
                'Cache-Control': 'max-age=0',
                'Upgrade-Insecure-Requests': '1',
                'Referer': 'https://leetcode.com/accounts/login/',
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_11_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/54.0.2840.98 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'zh-CN,zh;q=0.8,en;q=0.6',
                'Connection': 'keep-alive'
            }
        )

    def login(self):
        browser_cookies = {}
        if os.path.isfile(COOKIE_PATH):
            with open(COOKIE_PATH, 'rb') as f:
                browser_cookies = pickle.load(f)
        else:
            print("😎 Starting browser login..., please fill the login form")
            try:
                # browser login
                login_url = "https://leetcode.com/accounts/login"
                self.browser.get(login_url)

                WebDriverWait(self.browser, 24 * 60 * 3600).until(
                    lambda driver: driver.current_url.find("login") < 0
                )
                browser_cookies = self.browser.get_cookies()
                with open(COOKIE_PATH, 'wb') as f:
                    pickle.dump(browser_cookies, f)
                print("🎉 Login successfully")

            except Exception as e:
                print(f"🤔 Login Failed: {e}, please try again")
                exit()

        cookies = RequestsCookieJar()
        for item in browser_cookies:
            cookies.set(item['name'], item['value'])

            if item['name'] == 'csrftoken':
                self.session.headers.update({
                    "x-csrftoken": item['value']
                })

        self.session.cookies.update(cookies)

    def fetch_accepted_problems(self):
        response = self.session.get("https://leetcode.com/api/problems/all/")
        all_problems = json.loads(response.content.decode('utf-8'))
        # filter AC problems
        counter = 0
        for item in all_problems['stat_status_pairs']:
            if item['status'] == 'ac':
                id, slug = destructure(item['stat'], "question_id", "question__title_slug")
                # only update problem if not exists
                if Problem.get_or_none(Problem.id == id) is None:
                    counter += 1
                    # fetch problem
                    do(self.fetch_problem, args=[slug, True])
                    # fetch solution
                    do(self.fetch_solution, args=[slug])

                # only update submission if newer submissions exist
                do(self.check_and_fetch_submission, args=[slug, id])
        print(f"🤖 Updated {counter} problems")

    def fetch_problem(self, slug, accepted=False):
        print(f"🤖 Fetching problem: https://leetcode.com/problem/{slug}/...")
        query_params = {
            'operationName': "getQuestionDetail",
            'variables': {'titleSlug': slug},
            'query': '''query getQuestionDetail($titleSlug: String!) {
                        question(titleSlug: $titleSlug) {
                            questionId
                            questionFrontendId
                            questionTitle
                            questionTitleSlug
                            content
                            difficulty
                            stats
                            similarQuestions
                            categoryTitle
                            topicTags {
                            name
                            slug
                        }
                    }
                }'''
        }

        resp = self.session.post(
            "https://leetcode.com/graphql",
            data=json.dumps(query_params).encode('utf8'),
            headers={
                "content-type": "application/json",
            })
        body = json.loads(resp.content)

        # parse data
        question = get(body, 'data.question')

        Problem.replace(
            id=question['questionId'], display_id=question['questionFrontendId'], title=question["questionTitle"],
            level=question["difficulty"], slug=slug, description=question['content'],
            accepted=accepted
        ).execute()

        for item in question['topicTags']:
            if Tag.get_or_none(Tag.slug == item['slug']) is None:
                Tag.replace(
                    name=item['name'],
                    slug=item['slug']
                ).execute()

            ProblemTag.replace(
                problem=question['questionId'],
                tag=item['slug']
            ).execute()
        random_wait(10, 15)

    def fetch_solution(self, slug):
        print(f"🤖 Fetching solution for problem: {slug}")
        query_params = {
            "operationName": "QuestionNote",
            "variables": {"titleSlug": slug},
            "query": '''
            query QuestionNote($titleSlug: String!) {
                question(titleSlug: $titleSlug) {
                    questionId
                    article
                    solution {
                      id
                      content
                      contentTypeId
                      canSeeDetail
                      paidOnly
                      rating {
                        id
                        count
                        average
                        userRating {
                          score
                          __typename
                        }
                        __typename
                      }
                      __typename
                    }
                    __typename
                }
            }
            '''
        }
        resp = self.session.post("https://leetcode.com/graphql",
                                 data=json.dumps(query_params).encode('utf8'),
                                 headers={
                                     "content-type": "application/json",
                                 })
        body = json.loads(resp.content)

        # parse data
        solution = get(body, "data.question")
        solutionExist = solution['solution'] is not None and solution['solution']['paidOnly'] is False
        if solutionExist:
            Solution.replace(
                problem=solution['questionId'],
                url=f"https://leetcode.com/articles/{slug}/",
                content=solution['solution']['content']
            ).execute()
        random_wait(10, 15)

    def check_and_fetch_submission(self, slug, problem_id):
        """Check if there are newer submissions before fetching"""
        print(f"🔍 Checking for newer submissions for problem: {slug}")
        
        # Get the latest submission timestamp from database
        latest_submission = (Submission
                           .select()
                           .where(Submission.slug == slug)
                           .order_by(Submission.created.desc())
                           .first())
        
        latest_db_timestamp = latest_submission.created if latest_submission else 0
        
        # Fetch submission list from LeetCode API to check timestamps
        query_params = {
            'operationName': "Submissions",
            'variables': {"offset": 0, "limit": 5, "lastKey": '', "questionSlug": slug},
            'query': '''query Submissions($offset: Int!, $limit: Int!, $lastKey: String, $questionSlug: String!) {
                                        submissionList(offset: $offset, limit: $limit, lastKey: $lastKey, questionSlug: $questionSlug) {
                                        submissions {
                                            id
                                            statusDisplay
                                            timestamp
                                            __typename
                                        }
                                        __typename
                                    }
                                }'''
        }
        
        try:
            resp = self.session.post("https://leetcode.com/graphql",
                                   data=json.dumps(query_params).encode('utf8'),
                                   headers={"content-type": "application/json"})
            body = json.loads(resp.content)
            submissions = get(body, "data.submissionList.submissions")
            
            if not submissions:
                print(f"⚠️  No submissions found for {slug}")
                return
            
            # Check if there are any newer accepted submissions
            has_newer = False
            for sub in submissions:
                if sub['statusDisplay'] == 'Accepted':
                    try:
                        # Convert both timestamps to integers for comparison
                        leetcode_timestamp = int(sub['timestamp']) if sub['timestamp'] else 0
                        db_timestamp = int(latest_db_timestamp) if latest_db_timestamp else 0
                        
                        if leetcode_timestamp > db_timestamp:
                            has_newer = True
                            break
                    except (ValueError, TypeError) as timestamp_error:
                        print(f"⚠️  Timestamp comparison error for {slug}: {timestamp_error}")
                        # If we can't compare timestamps, assume it's newer to be safe
                        has_newer = True
                        break
            
            if has_newer:
                print(f"🆕 Found newer submissions for {slug}, fetching...")
                self.fetch_submission(slug)
            else:
                print(f"✅ No newer submissions for {slug}, skipping")
                
        except Exception as e:
            print(f"❌ Error checking submissions for {slug}: {e}")
            # If we can't check, fetch anyway to be safe
            print(f"🔄 Fetching submissions anyway due to error")
            self.fetch_submission(slug)
        
        random_wait(5, 8)  # Shorter wait for just checking

    def fetch_submission(self, slug):
        print(f"🤖 Fetching submission for problem: {slug}")
        query_params = {
            'operationName': "Submissions",
            'variables': {"offset": 0, "limit": 20, "lastKey": '', "questionSlug": slug},
            'query': '''query Submissions($offset: Int!, $limit: Int!, $lastKey: String, $questionSlug: String!) {
                                        submissionList(offset: $offset, limit: $limit, lastKey: $lastKey, questionSlug: $questionSlug) {
                                        lastKey
                                        hasNext
                                        submissions {
                                            id
                                            statusDisplay
                                            lang
                                            runtime
                                            timestamp
                                            url
                                            isPending
                                            __typename
                                        }
                                        __typename
                                    }
                                }'''
        }
        resp = self.session.post("https://leetcode.com/graphql",
                                 data=json.dumps(query_params).encode('utf8'),
                                 headers={
                                     "content-type": "application/json",
                                 })
        body = json.loads(resp.content)

        # parse data
        submissions = get(body, "data.submissionList.submissions")
        if len(submissions) > 0:
            for sub in submissions:
                if Submission.get_or_none(Submission.id == sub['id']) is not None:
                    continue

                if sub['statusDisplay'] == 'Accepted':
                    try:
                        url = sub['url']
                        print(f"🔗 Opening submission URL: {url}")
                        self.browser.get(f'https://leetcode.com{url}')
                        
                        element = WebDriverWait(self.browser, 10).until(
                            EC.presence_of_element_located((By.ID, "result_date"))
                        )
                        
                        html = self.browser.page_source
                        pattern = re.compile(
                            r'submissionCode: \'(?P<code>.*)\',\n  editCodeUrl', re.S
                        )
                        matched = pattern.search(html)
                        code = matched.groupdict().get('code') if matched else None
                        
                        if code:
                            Submission.insert(
                                id=sub['id'],
                                slug=slug,
                                language=sub['lang'],
                                created=sub['timestamp'],
                                source=code.encode('utf-8')
                            ).execute()
                            print(f"✅ Saved submission {sub['id']} for {slug}")
                        else:
                            print(f"⚠️  Could not extract code for submission {sub['id']}")
                            
                    except Exception as browser_error:
                        print(f"❌ Browser error for submission {sub['id']}: {browser_error}")
                        print(f"🔄 Skipping this submission and continuing...")
                        continue
        random_wait(10, 15)


if __name__ == '__main__':
    create_tables()
    crawler = LeetCodeCrawler()
    crawler.login()
    crawler.fetch_accepted_problems()
