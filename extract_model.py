import zipfile, os
os.makedirs('models', exist_ok=True)
with zipfile.ZipFile(r'C:\Users\junha\OneDrive\basic\Desktop\postfab-bge-m3-final.zip', 'r') as z:
    z.extractall('models')
print('완료: models/postfab-bge-m3-final/')
