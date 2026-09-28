; Inno Setup script for the ad3-bench-server GUI installer (Windows)
; Build: iscc setup.iss /DAppVersion=0.1.0
; Output: installer-output\ad3-bench-server-0.1.0-windows-setup.exe

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{2D60ACEE-69DE-494A-A341-73B481B84EC1}
AppName=ad3-bench-server
AppVersion={#AppVersion}
AppPublisher=embedded-pro
AppPublisherURL=https://github.com/embedded-pro/ad3-waveforms-bench
AppSupportURL=https://github.com/embedded-pro/ad3-waveforms-bench/issues
AppUpdatesURL=https://github.com/embedded-pro/ad3-waveforms-bench/releases
DefaultDirName={autopf}\ad3-bench-server
DefaultGroupName=ad3-bench-server
AllowNoIcons=yes
; Place output next to the repository root so the workflow can find it easily
OutputDir=..\..\installer-output
OutputBaseFilename=ad3-bench-server-{#AppVersion}-windows-setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
; Run as user: no admin required for autopf on modern Windows
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayIcon={app}\ad3-bench-gui.exe

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon";  Description: "{cm:CreateDesktopIcon}"; \
      GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "startupicon";  Description: "Start the server in the tray when Windows starts"; \
      GroupDescription: "Startup options:"; Flags: unchecked

[Files]
Source: "..\..\dist\ad3-bench-gui.exe"; DestDir: "{app}"; \
        Flags: ignoreversion

[Icons]
Name: "{group}\ad3-bench-server";                    Filename: "{app}\ad3-bench-gui.exe"
Name: "{group}\{cm:UninstallProgram,ad3-bench-server}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\ad3-bench-server";              Filename: "{app}\ad3-bench-gui.exe"; \
      Tasks: desktopicon
Name: "{userstartup}\ad3-bench-server";              Filename: "{app}\ad3-bench-gui.exe"; \
      Parameters: "--start --minimized"; Tasks: startupicon

[Run]
Filename: "{app}\ad3-bench-gui.exe"; \
          Description: "{cm:LaunchProgram,ad3-bench-server}"; \
          Flags: nowait postinstall skipifsilent
