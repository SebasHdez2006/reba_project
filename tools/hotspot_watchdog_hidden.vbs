' Lanza run_hotspot_watchdog.bat en ventana oculta (Startup folder / campo.bat).
Set fso = CreateObject("Scripting.FileSystemObject")
repo = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
Set sh = CreateObject("WScript.Shell")
sh.Run "cmd /c """ & repo & "\tools\run_hotspot_watchdog.bat""", 0, False
