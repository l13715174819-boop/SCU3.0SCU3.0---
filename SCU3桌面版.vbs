' SCU3 桌面版 - 无黑框启动器
' 双击运行，无命令行窗口

Option Explicit

Dim WshShell, fso, strDir, pythonwPath, appPath

Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' 获取当前脚本所在目录
strDir = fso.GetParentFolderName(WScript.ScriptFullName)

' pythonw.exe 路径
pythonwPath = "C:\Users\若水\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\vm\tools\python\pythonw.exe"

' app.py 路径
appPath = fso.BuildPath(strDir, "app.py")

' 检查文件是否存在
If Not fso.FileExists(pythonwPath) Then
    MsgBox "找不到 pythonw.exe" & vbCrLf & pythonwPath, vbCritical, "SCU3 启动失败"
    WScript.Quit
End If

If Not fso.FileExists(appPath) Then
    MsgBox "找不到 app.py" & vbCrLf & appPath, vbCritical, "SCU3 启动失败"
    WScript.Quit
End If

' 切换到程序目录
WshShell.CurrentDirectory = strDir

' 无黑框启动
WshShell.Run """" & pythonwPath & """ """ & appPath & """", 0, False
