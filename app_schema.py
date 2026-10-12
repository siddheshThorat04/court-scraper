"""Create the sheets (cases, names, pages, failed) with headers in the spreadsheet behind SHEETS_URL.
python app_schema.py      Safe to run twice."""
from db import DB

DB()._call("init")
print("sheets ready: cases, names, pages, failed")
