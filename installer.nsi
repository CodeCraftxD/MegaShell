!include "MUI2.nsh"

Name "MegaShell"
OutFile "MegaShellSetup.exe"

InstallDir "$PROGRAMFILES\MegaShell"

RequestExecutionLevel admin

Icon "icon.ico"
UninstallIcon "icon.ico"

VIProductVersion "1.0.0.0"
VIAddVersionKey "ProductName" "MegaShell"
VIAddVersionKey "CompanyName" "Adam"
VIAddVersionKey "FileDescription" "MegaShell Installer"
VIAddVersionKey "FileVersion" "1.0.0"

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

Section "Install"

    SetOutPath "$INSTDIR"

    File "dist\MegaShell.exe"

    CreateDirectory "$SMPROGRAMS\MegaShell"

    CreateShortcut "$SMPROGRAMS\MegaShell\MegaShell.lnk" "$INSTDIR\MegaShell.exe"
    CreateShortcut "$DESKTOP\MegaShell.lnk" "$INSTDIR\MegaShell.exe"

    WriteUninstaller "$INSTDIR\Uninstall.exe"

    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\MegaShell" "DisplayName" "MegaShell"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\MegaShell" "UninstallString" "$INSTDIR\Uninstall.exe"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\MegaShell" "DisplayIcon" "$INSTDIR\MegaShell.exe"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\MegaShell" "Publisher" "Adam"
    WriteRegStr HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\MegaShell" "DisplayVersion" "1.0.0"

    WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\MegaShell" "NoModify" 1
    WriteRegDWORD HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\MegaShell" "NoRepair" 1

SectionEnd

Section "Uninstall"

    Delete "$INSTDIR\MegaShell.exe"
    Delete "$INSTDIR\Uninstall.exe"

    Delete "$DESKTOP\MegaShell.lnk"

    Delete "$SMPROGRAMS\MegaShell\MegaShell.lnk"
    RMDir "$SMPROGRAMS\MegaShell"

    DeleteRegKey HKLM "Software\Microsoft\Windows\CurrentVersion\Uninstall\MegaShell"

    RMDir "$INSTDIR"

SectionEnd