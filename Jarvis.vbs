Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\Users\PC\Documents\jarvis"
sh.Run """C:\Users\PC\AppData\Local\Programs\Python\Python312\pythonw.exe"" ""C:\Users\PC\Documents\jarvis\Main.py""", 0, False
