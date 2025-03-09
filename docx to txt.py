import pypandoc

# Example file:
docxFilename = r'D:\Folder_1\NMIMS\2nd Year\Sem - 4\TCS\Assignment Front Page TCS.docx'
output = pypandoc.convert_file(docxFilename, 'plain', outputfile="somefile.txt")
assert output == ""

print(output)