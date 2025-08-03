import argparse
from database import create_tables
from crawler import LeetCodeCrawler
from renderer import render_anki


def main():
    parser = argparse.ArgumentParser(description='LeetCode Anki Card Generator')
    parser.add_argument('--create-db', action='store_true', 
                       help='Create/update database by crawling LeetCode (default: full process)')
    parser.add_argument('--create-anki', action='store_true',
                       help='Generate Anki deck from existing database')
    parser.add_argument('--full', action='store_true',
                       help='Run full process: create database + generate Anki deck (default)')
    
    args = parser.parse_args()
    
    # If no specific flags, run full process (backward compatibility)
    if not (args.create_db or args.create_anki):
        args.full = True
    
    print("🚀 LeetCode Anki Generator")
    print("=" * 40)
    
    # Always ensure database tables exist
    create_tables()
    print("✅ Database tables ready")
    
    # Create/update database by crawling
    if args.create_db or args.full:
        print("\n📡 Starting LeetCode crawler...")
        worker = LeetCodeCrawler()
        worker.login()
        worker.fetch_accepted_problems()
        print("✅ Database updated successfully")
    
    # Generate Anki deck from database
    if args.create_anki or args.full:
        print("\n🎴 Generating Anki deck...")
        render_anki()
        print("✅ Anki deck generated successfully")
    
    print("\n🎉 Process completed!")


if __name__ == '__main__':
    main()