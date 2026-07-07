import zipfile, re, sys

def read_docx(path):
    with zipfile.ZipFile(path) as z:
        with z.open('word/document.xml') as f:
            content = f.read().decode('utf-8')
    texts = re.findall(r'<w:t[^>]*>(.*?)</w:t>', content, re.DOTALL)
    return ' '.join(texts)

path = sys.argv[1]
sys.stdout.reconfigure(encoding='utf-8')
print(read_docx(path))
