import sys
from pathlib import Path
import docx

def compile_document(txt_path_str: str, doc_path_str: str, title: str):
    p_txt = Path(txt_path_str)
    p_doc = Path(doc_path_str)
    
    if not p_txt.exists():
        print(f"Error: Content file {p_txt} does not exist.")
        return 1

    raw_text = p_txt.read_text(encoding="utf-8")
    doc = docx.Document()
    doc.add_heading(title, 0)
    doc.add_paragraph("Compiled autonomously by IRIS AI Assistant for Mr. Adarsh Dwivedi.")

    for sec in raw_text.split("SECTION:"):
        sec = sec.strip()
        if not sec:
            continue
        lines = [ln.strip() for ln in sec.splitlines() if ln.strip()]
        if not lines:
            continue
        doc.add_heading(lines[0].strip("# *"), level=1)
        for para in lines[1:]:
            doc.add_paragraph(para)

    p_doc.parent.mkdir(parents=True, exist_ok=True)
    doc.save(p_doc)
    print(f"Document successfully compiled with live research at: {p_doc}")
    return 0

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print("Usage: docx_compiler.py <txt_file> <docx_file> <title>")
        sys.exit(1)
    sys.exit(compile_document(sys.argv[1], sys.argv[2], sys.argv[3]))
