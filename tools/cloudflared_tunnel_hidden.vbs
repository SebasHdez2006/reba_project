' Lanza run_cloudflared.bat en ventana oculta (para Startup folder / Task Scheduler).
Set fso = CreateObject("Scripting.FileSystemObject")
repo = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
Set sh = CreateObject("WScript.Shell")
sh.Run "cmd /c """ & repo & "\tools\run_cloudflared.bat""", 0, False
