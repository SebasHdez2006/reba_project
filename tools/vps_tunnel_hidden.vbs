' Lanza el tunel SSH de REBA en ventana oculta (para Task Scheduler).
Set sh = CreateObject("WScript.Shell")
sh.Run "cmd /c """ & CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName) & "\vps_tunnel.bat"""", 0, False
