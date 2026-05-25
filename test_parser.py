import json
from src.parser import parse_document
# 1. Run the parser on a PDF (limiting to 2 pages for speed)
pdf_file = "sample.pdf" # Replace with your PDF path if different
pages_data = parse_document(pdf_file, max_pages=2)
# 2. Save the output to a JSON file
output_file = "parser_output.json"
with open(output_file, "w", encoding="utf-8") as f:
    json.dump(pages_data, f, indent=2, ensure_ascii=False)
print(f"✅ Successfully saved the parser output to {output_file}")