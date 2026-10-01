Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
root = fs.GetParentFolderName(WScript.ScriptFullName)
python = fs.BuildPath(root, ".venv\Scripts\pythonw.exe")
If Not fs.FileExists(python) Then
    MsgBox "Python environment missing. Please follow README.md first.", 48, "Stock Manager"
    WScript.Quit 2
End If
shell.CurrentDirectory = root
shell.Run Chr(34) & python & Chr(34) & " -m app.desktop", 1, False
