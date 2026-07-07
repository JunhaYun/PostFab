import zipfile
with zipfile.ZipFile(r'C:\Users\junha\OneDrive\basic\Desktop\postfab-bge-m3-final.zip', 'r') as z:
    for name in z.namelist():
        print(name)
