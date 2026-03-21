# Quanta Course File Downloader

This tool helps you download and organize your Quanta course files automatically.

What it does:
1. Opens Quanta login.
2. Waits for you to log in.
3. Reads all courses in your dashboard.
4. Creates one folder per course inside one semester folder.
5. Downloads course files into the correct course folder.

Current behavior:
1. Focuses on PDF and Moodle resource links.
2. Skips already existing files.
3. Auto-renames duplicates using (1), (2), and so on.

## Project Files

1. quanta_downloader.py
  Main script.
2. selectors.example.json
  Template config.
3. selectors.json
  Your actual config (create from template).
4. requirements.txt
  Python packages.

## Quick Start (Windows)

Step 1: Open terminal in this project folder.

Step 2: Create and activate virtual environment.

PowerShell:

   py -m venv .venv
   .\.venv\Scripts\Activate.ps1

CMD:

   py -m venv .venv
   .venv\Scripts\activate.bat

Step 3: Install dependencies.

   pip install -r requirements.txt
   python -m playwright install chromium

Step 4: Create your config.

   copy selectors.example.json selectors.json

Step 5: Run downloader.

   .venv\Scripts\python.exe quanta_downloader.py --config selectors.json

## Config Guide (selectors.json)

Important top-level fields:
1. base_url
  Example: https://quantaaws.bits-goa.ac.in/
2. login_url
  Example: https://quantaaws.bits-goa.ac.in/login/index.php
3. courses_page_url
  Example: https://quantaaws.bits-goa.ac.in/my/
4. semester_name
  Folder name for this semester.
5. download_root
  Base output directory.
6. headless
  Keep false to see browser while logging in.
7. verify_ssl
  false if your campus certificate causes SSL verify failures.
8. selectors
  CSS selectors used to read courses and files.

## First Run: What You Should See

Typical log flow:
1. Opening login page
2. Please complete login in the browser window
3. Login detected
4. Found N course(s)
5. Candidate file links found: N
6. Downloaded: file_name.pdf

If browser opens and folders are created, but no files are downloaded, check Troubleshooting.

## Output Folder Structure

Example:

   downloads/
    Semester-6/
      DATABASE SYSTEMS CS F212/
       Handout.pdf
       Chapter 1.pdf
      MICROPROC & INTERFACING CS F241/
       Lecture 2.pdf

## Troubleshooting

1. Error: Browser executable does not exist

Run:

   .venv\Scripts\python.exe -m playwright install chromium

2. Error: SSL certificate verify failed

Set this in selectors.json:

   "verify_ssl": false

Use this only for trusted internal/campus Quanta deployments.

3. Folders created, but no files downloaded

This means links were not matched or were not considered downloadable.
Send terminal lines that include:
1. Candidate file links found
2. Failed download lines

Then update selectors or file filtering rules.

4. No courses found

Course card selectors may not match your Quanta theme.
Update selectors.course_card, selectors.course_name, selectors.course_link.

## Share With Someone Else

Share only these files:
1. quanta_downloader.py
2. selectors.example.json
3. requirements.txt
4. README.md

Do not share:
1. .venv
2. downloads
3. Personal cookies, tokens, or account data

## Safety Notes

1. This script relies on your logged-in browser session.
2. Use only on your own account or with proper permission.
3. Keep verify_ssl true whenever possible; use false only if your network certificate chain is not publicly trusted.
