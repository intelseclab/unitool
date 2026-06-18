"""
unitool/privacy_toggles.py
Windows registry / service / scheduled-task toggle settings for the Privacy tab.
Provides: ToggleSetting, TOGGLE_SETTINGS, scan_toggle_states, apply_toggles
"""
import os
import sys
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Any

if sys.platform == 'win32':
    try:
        import winreg as _wr
    except ImportError:
        _wr = None
else:
    _wr = None


# ── Data types ────────────────────────────────────────────────────────────────

@dataclass
class RegOp:
    hive: str       # 'HKLM' | 'HKCU'
    subkey: str
    value: str
    privacy: Any    # data to write when hardening
    default: Any    # data to write when reverting; None = delete value on revert
    vtype: str = 'DWORD'  # 'DWORD' | 'SZ'


@dataclass
class SvcOp:
    name: str
    privacy_start: int = 4   # 4 = Disabled
    default_start: int = 2   # 2 = Auto (best-effort)


@dataclass
class TaskOp:
    """Windows scheduled task to disable/enable."""
    path: str   # e.g. r'\Microsoft\Windows\CEIP\Consolidator'


# ── Linux op types ────────────────────────────────────────────────────────────

@dataclass
class SystemdSvcOp:
    """Linux systemd service to mask/disable."""
    name: str
    privacy_state: str = 'masked'   # 'masked' | 'disabled'
    default_state: str = 'enabled'
    user: bool = False               # True = systemctl --user (no elevation)


@dataclass
class SysctlOp:
    """Linux kernel parameter via sysctl."""
    key: str
    privacy: str    # value when hardened, e.g. '2'
    default: str    # value when reverted,  e.g. '0'
    persist: bool = True  # write to /etc/sysctl.d/99-unitool-privacy.conf


@dataclass
class GSettingsOp:
    """GNOME gsettings key (no elevation needed — user-level)."""
    schema: str
    key: str
    privacy: str    # value when hardened, e.g. 'false'
    default: str    # value when reverted,  e.g. 'true'


@dataclass
class ToggleSetting:
    key: str
    label: str
    description: str
    category: str          # 'telemetry' | 'privacy' | 'features'
    reg_ops:    list = field(default_factory=list)
    svc_ops:    list = field(default_factory=list)
    task_ops:   list = field(default_factory=list)   # scheduled tasks
    bat_apply:  list = field(default_factory=list)   # raw bat lines for apply only
    bat_revert: list = field(default_factory=list)   # raw bat lines for revert only
    win_ver:       str  = 'both'    # 'both' | 'win10' | 'win11'  (Windows sub-version)
    os_filter:     str  = 'windows' # 'windows' | 'linux' | 'macos' | 'all'
    systemd_ops:   list = field(default_factory=list)   # SystemdSvcOp
    sysctl_ops:    list = field(default_factory=list)   # SysctlOp
    gsettings_ops: list = field(default_factory=list)   # GSettingsOp
    sh_apply:      list = field(default_factory=list)   # raw bash lines for apply
    sh_revert:     list = field(default_factory=list)   # raw bash lines for revert
    state: bool | None = None   # None=unknown  True=applied  False=not applied


# ── Compact factory helpers ───────────────────────────────────────────────────

LM = 'HKLM'
CU = 'HKCU'

def _ro(hive, subkey, value, privacy, default=0, vtype='DWORD') -> RegOp:
    return RegOp(hive=hive, subkey=subkey, value=value,
                 privacy=privacy, default=default, vtype=vtype)

def _so(name, ps=4, ds=2) -> SvcOp:
    return SvcOp(name=name, privacy_start=ps, default_start=ds)

def _to(path: str) -> TaskOp:
    return TaskOp(path=path)

# Linux helpers
def _lso(name, ps='masked', ds='enabled', user=False) -> SystemdSvcOp:
    return SystemdSvcOp(name=name, privacy_state=ps, default_state=ds, user=user)

def _ctl(key, priv, dflt, persist=True) -> SysctlOp:
    return SysctlOp(key=key, privacy=priv, default=dflt, persist=persist)

def _gs(schema, key, priv, dflt) -> GSettingsOp:
    return GSettingsOp(schema=schema, key=key, privacy=priv, default=dflt)


# ── Settings catalog ──────────────────────────────────────────────────────────

TOGGLE_SETTINGS: list[ToggleSetting] = [

    # ── Telemetry ─────────────────────────────────────────────────────────────

    ToggleSetting('tel_level', 'Windows Telemetry Level',
        'Limit diagnostic data sent to Microsoft to Security level (minimum).',
        'telemetry', reg_ops=[
            _ro(LM, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\DataCollection', 'AllowTelemetry', 0, 1),
            _ro(LM, r'SOFTWARE\Policies\Microsoft\Windows\DataCollection', 'AllowTelemetry', 0, 1),
        ]),

    ToggleSetting('tel_diagtrack', 'Connected Experiences & Telemetry (DiagTrack)',
        'Stop the DiagTrack service that collects and uploads telemetry data to Microsoft.',
        'telemetry', svc_ops=[_so('DiagTrack')]),

    ToggleSetting('tel_dmwap', 'WAP Push Message Routing',
        'Disable dmwappushservice — unnecessary on desktop PCs.',
        'telemetry', svc_ops=[_so('dmwappushservice')]),

    ToggleSetting('tel_compat_runner', 'Block CompatTelRunner.exe',
        'Redirect CompatTelRunner.exe to taskkill via IFEO so it cannot run and upload data.',
        'telemetry', reg_ops=[
            _ro(LM,
                r'SOFTWARE\Microsoft\Windows NT\CurrentVersion\Image File Execution Options\CompatTelRunner.exe',
                'Debugger', 'taskkill.exe', None, vtype='SZ'),
        ]),

    ToggleSetting('tel_tasks', 'Scheduled Telemetry Tasks',
        'Disable Windows scheduled tasks that collect compatibility and CEIP usage data.',
        'telemetry', task_ops=[
            _to(r'\Microsoft\Windows\Application Experience\Microsoft Compatibility Appraiser'),
            _to(r'\Microsoft\Windows\Application Experience\ProgramDataUpdater'),
            _to(r'\Microsoft\Windows\Autochk\Proxy'),
            _to(r'\Microsoft\Windows\Customer Experience Improvement Program\Consolidator'),
            _to(r'\Microsoft\Windows\Customer Experience Improvement Program\UsbCeip'),
            _to(r'\Microsoft\Windows\Customer Experience Improvement Program\KernelCeipTask'),
            _to(r'\Microsoft\Windows\DiskDiagnostic\Microsoft-Windows-DiskDiagnosticDataCollector'),
        ]),

    ToggleSetting('tel_error_report', 'Windows Error Reporting',
        'Stop WerSvc and disable automatic error report uploads to Microsoft.',
        'telemetry', reg_ops=[
            _ro(LM, r'SOFTWARE\Policies\Microsoft\Windows\Windows Error Reporting', 'Disabled', 1, 0),
        ], svc_ops=[_so('WerSvc', 4, 3)]),

    ToggleSetting('tel_siuf', 'Feedback & CEIP Requests',
        'Disable Windows Feedback prompts and Software Quality Metrics notifications.',
        'telemetry', reg_ops=[
            _ro(CU, r'SOFTWARE\Microsoft\Siuf\Rules', 'NumberOfSIUFInPeriod', 0, 1),
            _ro(LM, r'SOFTWARE\Policies\Microsoft\SQMClient\Windows', 'CEIPEnable', 0, 1),
        ]),

    ToggleSetting('tel_activity', 'Activity History & Timeline',
        'Stop Windows from recording and syncing your usage timeline to the cloud.',
        'telemetry', reg_ops=[
            _ro(LM, r'SOFTWARE\Policies\Microsoft\Windows\System', 'EnableActivityFeed', 0, 1),
            _ro(LM, r'SOFTWARE\Policies\Microsoft\Windows\System', 'PublishUserActivities', 0, 1),
            _ro(LM, r'SOFTWARE\Policies\Microsoft\Windows\System', 'UploadUserActivities', 0, 1),
        ]),

    ToggleSetting('tel_adid', 'Advertising ID',
        'Prevent apps from using your advertising ID to show personalised ads.',
        'telemetry', reg_ops=[
            _ro(CU, r'SOFTWARE\Microsoft\Windows\CurrentVersion\AdvertisingInfo', 'Enabled', 0, 1),
            _ro(LM, r'SOFTWARE\Policies\Microsoft\Windows\AdvertisingInfo', 'DisabledByGroupPolicy', 1, 0),
        ]),

    ToggleSetting('tel_tailored', 'Tailored Experiences',
        'Stop Microsoft from using your diagnostics data to personalise tips and ads.',
        'telemetry', reg_ops=[
            _ro(CU, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Privacy',
                'TailoredExperiencesWithDiagnosticDataEnabled', 0, 1),
        ]),

    ToggleSetting('tel_office', 'Office Telemetry',
        'Disable diagnostic data collection in Microsoft Office 2016 / 365.',
        'telemetry', reg_ops=[
            _ro(CU, r'SOFTWARE\Microsoft\Office\Common\ClientTelemetry', 'DisableTelemetry', 1, 0),
            _ro(CU, r'SOFTWARE\Microsoft\Office\16.0\Common\ClientTelemetry', 'DisableTelemetry', 1, 0),
            _ro(CU, r'SOFTWARE\Policies\Microsoft\Office\16.0\Common', 'sendcustomerdata', 0, 1),
        ]),

    ToggleSetting('tel_vs', 'Visual Studio Telemetry',
        'Disable diagnostic data and feedback in Visual Studio IDEs.',
        'telemetry', reg_ops=[
            _ro(CU, r'SOFTWARE\Microsoft\VisualStudio\Telemetry', 'TurnOffSwitch', 1, 0),
        ], svc_ops=[_so('VSStandardCollectorService150', 4, 3)]),

    ToggleSetting('tel_nvidia', 'NVIDIA Telemetry',
        'Stop the NVIDIA telemetry container service.',
        'telemetry', svc_ops=[_so('NvTelemetryContainer', 4, 2)]),

    ToggleSetting('tel_chrome', 'Chrome Telemetry',
        'Disable metrics and usage reporting in Google Chrome via Group Policy.',
        'telemetry', reg_ops=[
            _ro(LM, r'SOFTWARE\Policies\Google\Chrome', 'MetricsReportingEnabled', 0, 1),
            _ro(LM, r'SOFTWARE\Policies\Google\Chrome', 'UserFeedbackAllowed', 0, 1),
        ]),

    ToggleSetting('tel_firefox', 'Firefox Telemetry',
        'Disable telemetry and default browser agent reporting in Mozilla Firefox.',
        'telemetry', reg_ops=[
            _ro(LM, r'SOFTWARE\Policies\Mozilla\Firefox', 'DisableTelemetry', 1, 0),
            _ro(LM, r'SOFTWARE\Policies\Mozilla\Firefox', 'DisableDefaultBrowserAgent', 1, 0),
        ]),

    ToggleSetting('tel_edge', 'Microsoft Edge Telemetry',
        'Disable metrics, personalisation, and feedback reporting in Microsoft Edge.',
        'telemetry', reg_ops=[
            _ro(LM, r'SOFTWARE\Policies\Microsoft\Edge', 'MetricsReportingEnabled', 0, 1),
            _ro(LM, r'SOFTWARE\Policies\Microsoft\Edge', 'UserFeedbackAllowed', 0, 1),
            _ro(LM, r'SOFTWARE\Policies\Microsoft\Edge', 'PersonalizationReportingEnabled', 0, 1),
        ]),

    # ── Privacy Settings ──────────────────────────────────────────────────────

    ToggleSetting('prv_location', 'Location Services',
        'Disable Windows location tracking and the location sensor service (lfsvc).',
        'privacy', reg_ops=[
            _ro(LM, r'SOFTWARE\Policies\Microsoft\Windows\LocationAndSensors', 'DisableLocation', 1, 0),
            _ro(LM, r'SOFTWARE\Policies\Microsoft\Windows\LocationAndSensors', 'DisableLocationScripting', 1, 0),
        ], svc_ops=[_so('lfsvc', 4, 3)]),

    ToggleSetting('prv_wifi_sense', 'Wi-Fi Sense',
        'Disable automatic connection to open/shared Wi-Fi networks.',
        'privacy', reg_ops=[
            _ro(LM, r'SOFTWARE\Microsoft\WcmSvc\wifinetworkmanager\config', 'AutoConnectAllowedOEM', 0, 1),
        ]),

    ToggleSetting('prv_cloud_clip', 'Cloud Clipboard Sync',
        'Prevent clipboard history from syncing across devices via Microsoft account.',
        'privacy', reg_ops=[
            _ro(LM, r'SOFTWARE\Policies\Microsoft\Windows\System', 'AllowClipboardHistory', 0, 1),
            _ro(LM, r'SOFTWARE\Policies\Microsoft\Windows\System', 'AllowCrossDeviceClipboard', 0, 1),
        ]),

    ToggleSetting('prv_cortana', 'Cortana & Web Search',
        'Disable Cortana voice assistant and web search integration in Windows Search.',
        'privacy', reg_ops=[
            _ro(LM, r'SOFTWARE\Policies\Microsoft\Windows\Windows Search', 'AllowCortana', 0, 1),
            _ro(LM, r'SOFTWARE\Policies\Microsoft\Windows\Windows Search', 'DisableWebSearch', 1, 0),
            _ro(LM, r'SOFTWARE\Policies\Microsoft\Windows\Windows Search', 'ConnectedSearchUseWeb', 0, 1),
        ]),

    ToggleSetting('prv_speech', 'Online Speech Recognition',
        'Disable microphone-based speech data collection for personalisation.',
        'privacy', reg_ops=[
            _ro(CU, r'SOFTWARE\Microsoft\Speech_OneCore\Settings\OnlineSpeechPrivacy', 'HasAccepted', 0, 1),
            _ro(LM, r'SOFTWARE\Policies\Microsoft\InputPersonalization', 'AllowInputPersonalization', 0, 1),
        ]),

    ToggleSetting('prv_inking', 'Inking & Typing Personalisation',
        'Stop Windows from collecting handwriting and typing samples.',
        'privacy', reg_ops=[
            _ro(CU, r'SOFTWARE\Microsoft\InputPersonalization', 'RestrictImplicitInkCollection', 1, 0),
            _ro(CU, r'SOFTWARE\Microsoft\InputPersonalization', 'RestrictImplicitTextCollection', 1, 0),
            _ro(CU, r'SOFTWARE\Microsoft\Personalization\Settings', 'AcceptedPrivacyPolicy', 0, 1),
        ]),

    ToggleSetting('prv_find_device', 'Find My Device',
        'Disable the location-based device tracking feature.',
        'privacy', reg_ops=[
            _ro(LM, r'SOFTWARE\Policies\Microsoft\FindMyDevice', 'AllowFindMyDevice', 0, 1),
        ]),

    ToggleSetting('prv_biometrics', 'Windows Hello Biometrics',
        'Disable biometric authentication (fingerprint, face recognition) system-wide.',
        'privacy', reg_ops=[
            _ro(LM, r'SOFTWARE\Policies\Microsoft\Biometrics', 'Enabled', 0, 1),
        ]),

    ToggleSetting('prv_auto_signin', 'Auto Sign-in After Restart',
        'Prevent Windows from automatically signing in your account after update restarts.',
        'privacy', reg_ops=[
            _ro(LM, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System',
                'DisableAutomaticRestartSignOn', 1, 0),
        ]),

    ToggleSetting('prv_projection', 'Projecting to This PC',
        'Block other devices from wirelessly casting or projecting to this computer.',
        'privacy', reg_ops=[
            _ro(LM, r'SOFTWARE\Policies\Microsoft\Windows\Connect', 'AllowProjectionToPC', 0, 1),
        ]),

    ToggleSetting('prv_spotlight', 'Lock Screen Spotlight & Ads',
        'Disable Windows Spotlight (Microsoft promotions and tips) on the lock screen.',
        'privacy', reg_ops=[
            _ro(CU, r'SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager',
                'RotatingLockScreenEnabled', 0, 1),
            _ro(CU, r'SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager',
                'RotatingLockScreenOverlayEnabled', 0, 1),
        ]),

    ToggleSetting('prv_phone_link', 'Phone Link (Cross-Device Platform)',
        'Disable Cross-Device Platform used to sync notifications with Android/iOS.',
        'privacy', reg_ops=[
            _ro(LM, r'SOFTWARE\Policies\Microsoft\Windows\System', 'EnableMmx', 0, 1),
            _ro(LM, r'SOFTWARE\Policies\Microsoft\Windows\System', 'EnableCdp', 0, 1),
        ]),

    ToggleSetting('prv_copilot', 'Copilot & AI Data Collection',
        'Disable Windows Copilot, AI Recall, and related cloud data analysis.',
        'privacy', reg_ops=[
            _ro(LM, r'SOFTWARE\Policies\Microsoft\Windows\WindowsCopilot', 'TurnOffWindowsCopilot', 1, 0),
            _ro(CU, r'SOFTWARE\Policies\Microsoft\Windows\WindowsAI', 'DisableAIDataAnalysis', 1, 0),
        ], win_ver='win11'),

    ToggleSetting('prv_explorer_ads', 'File Explorer Sync Provider Ads',
        'Hide "Get more storage" and OneDrive sync ads shown inside File Explorer.',
        'privacy', reg_ops=[
            _ro(CU, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Advanced',
                'ShowSyncProviderNotifications', 0, 1),
        ], win_ver='win11'),

    # ── Windows Features ──────────────────────────────────────────────────────

    # Services
    ToggleSetting('svc_print', 'Print Spooler Service',
        'Set Print Spooler to manual (demand) start — saves resources on non-printing PCs.',
        'features', svc_ops=[_so('Spooler', 3, 2)]),

    ToggleSetting('svc_wmp', 'WMP Network Sharing Service',
        'Disable Windows Media Player network sharing service (WMPNetworkSvc).',
        'features', svc_ops=[_so('WMPNetworkSvc', 4, 3)]),

    ToggleSetting('svc_fax', 'Fax Service',
        'Disable the legacy Fax service — not needed on modern PCs.',
        'features', svc_ops=[_so('Fax', 4, 3)]),

    ToggleSetting('svc_insider', 'Windows Insider Service',
        'Disable the Windows Insider Program service (wisvc) on stable-channel PCs.',
        'features', svc_ops=[_so('wisvc', 4, 3)]),

    ToggleSetting('svc_sensors', 'Sensor Services',
        'Disable motion sensor services (accelerometer, gyroscope) — rarely needed on desktops.',
        'features', svc_ops=[_so('SensrSvc', 4, 3), _so('SensorService', 4, 3)]),

    # Taskbar / UI
    ToggleSetting('feat_taskbar_search', 'Taskbar Search Button',
        'Hide the search icon from the taskbar (0 = hidden, 1 = icon, 2 = search box).',
        'features', reg_ops=[
            _ro(CU, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Search', 'SearchboxTaskbarMode', 0, 2),
        ]),

    ToggleSetting('feat_my_people', 'My People / People Band',
        'Remove the My People shortcut from the taskbar in Windows 10.',
        'features', reg_ops=[
            _ro(CU, r'Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced', 'PeopleBand', 0, 1),
        ], win_ver='win10'),

    ToggleSetting('feat_menu_delay', 'Menu Display Delay',
        'Set menu popup delay to 0 ms for instant menus (Windows default is 400 ms).',
        'features', reg_ops=[
            _ro(CU, r'Control Panel\Desktop', 'MenuShowDelay', '0', '400', vtype='SZ'),
        ]),

    ToggleSetting('feat_tray_icons', 'Show All System Tray Icons',
        'Always show all notification area icons instead of hiding them automatically.',
        'features', reg_ops=[
            _ro(CU, r'Software\Microsoft\Windows\CurrentVersion\Explorer', 'EnableAutoTray', 0, 1),
        ]),

    ToggleSetting('feat_classic_menu', 'Classic Right-Click Menu (Windows 11)',
        'Restore the full classic context menu instead of the simplified Windows 11 version.',
        'features', win_ver='win11',
        bat_apply=[
            r'reg add "HKCU\Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}\InprocServer32"'
            r' /ve /t REG_SZ /d "" /f >nul 2>&1',
        ],
        bat_revert=[
            r'reg delete "HKCU\Software\Classes\CLSID\{86ca1aa0-34aa-4e8b-a509-50c905bae2a2}" /f >nul 2>&1',
        ]),

    ToggleSetting('feat_cast_remove', 'Cast to Device Context Menu',
        'Remove the "Cast to Device" option from the right-click context menu.',
        'features', reg_ops=[
            _ro(LM, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Shell Extensions\Blocked',
                '{7AD84985-87B4-4a16-BE58-8B72A5B390F7}', '', None, vtype='SZ'),
        ]),

    # Game / entertainment
    ToggleSetting('feat_gamebar', 'Game Bar & Game DVR',
        'Disable Xbox Game Bar and background game clip / screenshot recording.',
        'features', reg_ops=[
            _ro(CU, r'SOFTWARE\Microsoft\Windows\CurrentVersion\GameDVR', 'AppCaptureEnabled', 0, 1),
            _ro(LM, r'SOFTWARE\Policies\Microsoft\Windows\GameDVR', 'AllowGameDVR', 0, 1),
        ]),

    ToggleSetting('feat_gaming_mode', 'Windows Game Mode',
        'Disable Game Mode — OS will not automatically prioritise game processes.',
        'features', reg_ops=[
            _ro(CU, r'Software\Microsoft\GameBar', 'AutoGameModeEnabled', 0, 1),
        ]),

    ToggleSetting('feat_xbox', 'Xbox Live Services',
        'Disable Xbox authentication, game-save, and network API services.',
        'features', svc_ops=[
            _so('XblAuthManager', 4, 3),
            _so('XblGameSave', 4, 3),
            _so('XboxGipSvc', 4, 3),
            _so('XboxNetApiSvc', 4, 3),
        ]),

    # Start / notifications
    ToggleSetting('feat_start_ads', 'Start Menu Suggestions & Ads',
        'Remove Microsoft-promoted apps and pre-installed app suggestions from Start.',
        'features', reg_ops=[
            _ro(CU, r'SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager',
                'SystemPaneSuggestionsEnabled', 0, 1),
            _ro(CU, r'SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager',
                'SubscribedContentEnabled', 0, 1),
            _ro(CU, r'SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager',
                'SoftLandingEnabled', 0, 1),
            _ro(CU, r'SOFTWARE\Microsoft\Windows\CurrentVersion\ContentDeliveryManager',
                'OemPreInstalledAppsEnabled', 0, 1),
        ]),

    ToggleSetting('feat_widgets', 'Taskbar Widgets (Windows 11)',
        'Remove the Widgets button from the Windows 11 taskbar.',
        'features', reg_ops=[
            _ro(CU, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Advanced', 'TaskbarDa', 0, 1),
        ], win_ver='win11'),

    ToggleSetting('feat_news', 'Taskbar News & Interests Feed',
        'Disable the weather and news feed widget on the taskbar.',
        'features', reg_ops=[
            _ro(CU, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Feeds', 'ShellFeedsTaskbarViewMode', 2, 0),
        ], win_ver='win10'),

    ToggleSetting('feat_chat', 'Chat / Meet Now Taskbar Icon',
        'Remove the Meet Now and Microsoft Teams Chat icons from the taskbar.',
        'features', reg_ops=[
            _ro(CU, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\Explorer', 'HideSCAMeetNow', 1, 0),
            _ro(CU, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Advanced', 'TaskbarMn', 0, 1),
        ]),

    # File Explorer
    ToggleSetting('feat_quick_access', 'Quick Access Recent & Frequent Files',
        'Stop File Explorer from showing recent files and frequent folders in Quick Access.',
        'features', reg_ops=[
            _ro(CU, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer', 'ShowRecent', 0, 1),
            _ro(CU, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer', 'ShowFrequent', 0, 1),
        ]),

    ToggleSetting('feat_compact_mode', 'File Explorer Compact Mode',
        'Enable compact view in File Explorer for more items visible at once (Windows 11).',
        'features', reg_ops=[
            _ro(CU, r'Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced', 'UseCompactMode', 1, 0),
        ], win_ver='win11'),

    ToggleSetting('feat_long_paths', 'Long Path Support (> 260 characters)',
        'Remove the 260-character path length limit in Windows (requires app support).',
        'features', reg_ops=[
            _ro(LM, r'SYSTEM\CurrentControlSet\Control\FileSystem', 'LongPathsEnabled', 1, 0),
        ]),

    # Misc system
    ToggleSetting('feat_search_svc', 'Windows Search Indexing Service',
        'Disable WSearch background indexing to save memory (manual search still works).',
        'features', svc_ops=[_so('WSearch', 4, 2)]),

    ToggleSetting('feat_superfetch', 'SysMain / Superfetch',
        'Disable SysMain which pre-caches frequently used apps into RAM.',
        'features', svc_ops=[_so('SysMain', 4, 2)]),

    ToggleSetting('feat_ink', 'Windows Ink Workspace',
        'Disable the pen and stylus ink workspace panel.',
        'features', reg_ops=[
            _ro(LM, r'SOFTWARE\Policies\Microsoft\WindowsInkWorkspace', 'AllowWindowsInkWorkspace', 0, 1),
        ]),

    ToggleSetting('feat_snap', 'Snap Assist Layout Flyout',
        'Disable the snap layout grid shown when hovering the maximise button (Windows 11).',
        'features', reg_ops=[
            _ro(CU, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Advanced',
                'EnableSnapAssistFlyout', 0, 1),
        ], win_ver='win11'),

    ToggleSetting('feat_utc_clock', 'Hardware Clock as UTC',
        'Set the hardware clock to UTC — prevents time desync in dual-boot Linux/Windows setups.',
        'features', reg_ops=[
            _ro(LM, r'SYSTEM\CurrentControlSet\Control\TimeZoneInformation', 'RealTimeIsUniversal', 1, 0),
        ]),

    ToggleSetting('feat_smb1', 'SMB1 Protocol',
        'Disable the legacy SMBv1 file-sharing protocol — a well-known security risk.',
        'features', reg_ops=[
            _ro(LM, r'SYSTEM\CurrentControlSet\Services\LanmanServer\Parameters', 'SMB1', 0, 1),
        ]),

    ToggleSetting('feat_sticky_keys', 'Sticky Keys Accessibility Prompt',
        'Disable the Shift×5 shortcut that triggers the Sticky Keys accessibility dialog.',
        'features', reg_ops=[
            _ro(CU, r'Control Panel\Accessibility\StickyKeys', 'Flags', '506', '510', vtype='SZ'),
        ]),

    ToggleSetting('feat_homegroup', 'HomeGroup Service',
        'Disable the legacy HomeGroup network sharing service.',
        'features', reg_ops=[
            _ro(LM, r'SOFTWARE\Policies\Microsoft\Windows\HomeGroup', 'DisableHomeGroup', 1, 0),
        ], svc_ops=[_so('HomeGroupProvider', 4, 3)], win_ver='win10'),

    ToggleSetting('feat_onedrive', 'OneDrive File Sync Policy',
        'Disable OneDrive file sync via Group Policy (does not uninstall OneDrive).',
        'features', reg_ops=[
            _ro(LM, r'SOFTWARE\Policies\Microsoft\Windows\OneDrive', 'DisableFileSyncNGSC', 1, 0),
        ]),

    # Windows Update
    ToggleSetting('upd_store', 'Microsoft Store Auto-Updates',
        'Disable automatic app updates from the Microsoft Store.',
        'features', reg_ops=[
            _ro(LM, r'SOFTWARE\Policies\Microsoft\WindowsStore', 'AutoDownload', 2, 4),
        ]),

    ToggleSetting('upd_drivers', 'Driver Updates via Windows Update',
        'Exclude hardware drivers from being updated automatically through Windows Update.',
        'features', reg_ops=[
            _ro(LM, r'SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate',
                'ExcludeWUDriversInQualityUpdate', 1, 0),
        ]),

    ToggleSetting('feat_copilot_btn', 'Copilot Button in Taskbar',
        'Hide the Copilot shortcut button from the Windows 11 taskbar.',
        'features', reg_ops=[
            _ro(CU, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Advanced',
                'ShowCopilotButton', 0, 1),
        ], win_ver='win11'),

    ToggleSetting('feat_taskbar_preview', 'Taskbar Thumbnail Previews',
        'Disable live window previews when hovering over taskbar buttons.',
        'features', reg_ops=[
            _ro(CU, r'SOFTWARE\Microsoft\Windows\CurrentVersion\Explorer\Advanced',
                'TaskbarPreview', 0, 1),
        ]),

    # ── Linux Telemetry ───────────────────────────────────────────────────────

    ToggleSetting('lnx_tel_whoopsie', 'Ubuntu Crash Reporter (whoopsie)',
        'Mask whoopsie and apport — services that send crash reports to Canonical.',
        'telemetry', os_filter='linux',
        systemd_ops=[_lso('whoopsie'), _lso('apport')]),

    ToggleSetting('lnx_tel_abrt', 'Fedora / RHEL Bug Reporter (abrt)',
        'Mask the Automatic Bug Reporting Tool — sends crash data to Red Hat.',
        'telemetry', os_filter='linux',
        systemd_ops=[_lso('abrtd'), _lso('abrt-ccpp')]),

    ToggleSetting('lnx_tel_popcon', 'Ubuntu Popularity Contest',
        'Disable popularity-contest — periodically submits your installed packages to Canonical.',
        'telemetry', os_filter='linux',
        systemd_ops=[_lso('popularity-contest', 'disabled', 'enabled')]),

    ToggleSetting('lnx_tel_fwupd', 'fwupd Telemetry Reports',
        'Mask fwupd-refresh so firmware metadata and reports are not sent automatically.',
        'telemetry', os_filter='linux',
        systemd_ops=[_lso('fwupd-refresh.timer', 'masked', 'enabled')]),

    ToggleSetting('lnx_tel_gnome', 'GNOME Diagnostic Reports',
        'Stop GNOME from sending technical problem reports and software usage stats.',
        'telemetry', os_filter='linux',
        gsettings_ops=[
            _gs('org.gnome.desktop.privacy', 'report-technical-problems', 'false', 'true'),
            _gs('org.gnome.desktop.privacy', 'send-software-usage-stats', 'false', 'true'),
        ]),

    # ── Linux Privacy ─────────────────────────────────────────────────────────

    ToggleSetting('lnx_prv_ipv6', 'IPv6 Privacy Extensions',
        'Use temporary randomised IPv6 addresses to prevent long-term tracking.',
        'privacy', os_filter='linux',
        sysctl_ops=[
            _ctl('net.ipv6.conf.all.use_tempaddr', '2', '0'),
            _ctl('net.ipv6.conf.default.use_tempaddr', '2', '0'),
        ]),

    ToggleSetting('lnx_prv_tcp_ts', 'TCP Timestamps',
        'Disable TCP timestamps to reduce OS fingerprinting by remote hosts.',
        'privacy', os_filter='linux',
        sysctl_ops=[_ctl('net.ipv4.tcp_timestamps', '0', '1')]),

    ToggleSetting('lnx_prv_aslr', 'Full Kernel ASLR',
        'Enable full address space layout randomisation (value 2) to harden against exploits.',
        'privacy', os_filter='linux',
        sysctl_ops=[_ctl('kernel.randomize_va_space', '2', '1')]),

    ToggleSetting('lnx_prv_location', 'GNOME Location Services',
        'Disable GNOME system-wide location access via GeoClue.',
        'privacy', os_filter='linux',
        gsettings_ops=[
            _gs('org.gnome.system.location', 'enabled', 'false', 'true'),
        ]),

    ToggleSetting('lnx_prv_core_dumps', 'Core Dumps',
        'Redirect core dumps to /dev/null so crash data is not written to disk.',
        'privacy', os_filter='linux',
        sysctl_ops=[_ctl('kernel.core_pattern', '|/bin/false', 'core', persist=True)]),

    # ── Linux Features ────────────────────────────────────────────────────────

    ToggleSetting('lnx_feat_auto_upgrade', 'APT Automatic Upgrades',
        'Disable the apt-daily-upgrade timer that installs updates in the background.',
        'features', os_filter='linux',
        systemd_ops=[
            _lso('apt-daily-upgrade.timer',    'masked', 'enabled'),
            _lso('apt-daily.timer',            'masked', 'enabled'),
            _lso('unattended-upgrades.service', 'disabled', 'enabled'),
        ]),

    ToggleSetting('lnx_feat_tracker', 'GNOME Tracker Indexer',
        'Disable GNOME Tracker file-indexing services (user session — no elevation needed).',
        'features', os_filter='linux',
        systemd_ops=[
            _lso('tracker-miner-fs-3.service',  'masked', 'enabled', user=True),
            _lso('tracker-writeback-3.service', 'masked', 'enabled', user=True),
            _lso('tracker-xdg-portal-3.service','masked', 'enabled', user=True),
        ]),

    ToggleSetting('lnx_feat_crash_notif', 'GNOME Crash Notification',
        'Suppress the GNOME dialog that appears when an application crashes.',
        'features', os_filter='linux',
        gsettings_ops=[
            _gs('org.gnome.desktop.privacy', 'report-technical-problems', 'false', 'true'),
        ]),
]


# ── OS filter ─────────────────────────────────────────────────────────────────

def os_matches(os_filter: str) -> bool:
    """Return True if os_filter applies to the current platform."""
    if os_filter == 'all':     return True
    if os_filter == 'windows': return sys.platform == 'win32'
    if os_filter == 'linux':   return sys.platform == 'linux'
    if os_filter == 'macos':   return sys.platform == 'darwin'
    return False


# ── Windows version detection ─────────────────────────────────────────────────

def get_win_build() -> int:
    """Return Windows build number (e.g. 19045 = Win10, 22621 = Win11)."""
    if sys.platform != 'win32':
        return 0
    try:
        return sys.getwindowsversion().build
    except Exception:
        return 0


def is_win11() -> bool:
    return get_win_build() >= 22000


# ── Registry / service / task read helpers ────────────────────────────────────

def _reg_read(hive: str, subkey: str, value: str) -> Any:
    if not _wr:
        return None
    hk = _wr.HKEY_LOCAL_MACHINE if hive == 'HKLM' else _wr.HKEY_CURRENT_USER
    try:
        with _wr.OpenKey(hk, subkey) as k:
            data, _ = _wr.QueryValueEx(k, value)
            return data
    except OSError:
        return None


def _svc_start(name: str) -> int | None:
    if not _wr:
        return None
    try:
        with _wr.OpenKey(_wr.HKEY_LOCAL_MACHINE,
                         f'SYSTEM\\CurrentControlSet\\Services\\{name}') as k:
            v, _ = _wr.QueryValueEx(k, 'Start')
            return int(v)
    except OSError:
        return None


# ── Linux helpers ─────────────────────────────────────────────────────────────

def _systemd_state(name: str, user: bool = False) -> str | None:
    """Return systemd unit state string ('enabled','disabled','masked',…) or None."""
    try:
        cmd = ['systemctl']
        if user:
            cmd.append('--user')
        cmd += ['is-enabled', '--quiet', name]
        # is-enabled exits 0=enabled, 1=disabled/masked/not-found
        # We need the actual state string, so use --no-pager + non-quiet form
        cmd2 = ['systemctl'] + (['--user'] if user else []) + ['is-enabled', name]
        r = subprocess.run(cmd2, capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or None
    except Exception:
        return None


def _sysctl_val(key: str) -> str | None:
    try:
        r = subprocess.run(['sysctl', '-n', key],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() if r.returncode == 0 else None
    except Exception:
        return None


def _gsettings_val(schema: str, key: str) -> str | None:
    try:
        r = subprocess.run(['gsettings', 'get', schema, key],
                           capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return r.stdout.strip().strip("'")
        return None
    except Exception:
        return None


def _task_is_disabled(path: str) -> bool | None:
    """Return True if task is disabled, False if enabled, None if not found."""
    try:
        r = subprocess.run(
            ['schtasks', '/Query', '/TN', path, '/FO', 'LIST'],
            capture_output=True, text=True,
            creationflags=0x08000000, timeout=8,
        )
        if r.returncode != 0:
            return None  # task doesn't exist on this Windows version
        return 'Disabled' in r.stdout
    except Exception:
        return None


# ── Public API ────────────────────────────────────────────────────────────────

def scan_toggle_states(settings: list[ToggleSetting]) -> list[ToggleSetting]:
    """Read current registry / service / task / sysctl / gsettings state."""
    for s in settings:
        if sys.platform == 'win32':
            _scan_windows(s)
        elif sys.platform == 'linux':
            _scan_linux(s)
        else:
            s.state = None
    return settings


def _scan_windows(s: ToggleSetting):
    if not s.reg_ops and not s.svc_ops and not s.task_ops:
        s.state = None
        return
    total = len(s.reg_ops) + len(s.svc_ops) + len(s.task_ops)
    hits  = 0
    for op in s.reg_ops:
        cur = _reg_read(op.hive, op.subkey, op.value)
        if cur is not None and cur == op.privacy:
            hits += 1
    for op in s.svc_ops:
        cur = _svc_start(op.name)
        if cur is not None and cur == op.privacy_start:
            hits += 1
    for op in s.task_ops:
        disabled = _task_is_disabled(op.path)
        if disabled is True:
            hits += 1
        elif disabled is None:
            total -= 1
    s.state = None if total == 0 else (hits == total)


def _scan_linux(s: ToggleSetting):
    ops = s.systemd_ops + s.sysctl_ops + s.gsettings_ops
    if not ops:
        s.state = None
        return
    total = len(ops)
    hits  = 0
    for op in s.systemd_ops:
        st = _systemd_state(op.name, op.user)
        if st in ('masked', 'disabled'):
            hits += 1
        elif st is None:
            total -= 1  # service absent on this distro
    for op in s.sysctl_ops:
        val = _sysctl_val(op.key)
        if val is not None and val == op.privacy:
            hits += 1
        elif val is None:
            total -= 1
    for op in s.gsettings_ops:
        val = _gsettings_val(op.schema, op.key)
        if val is not None and val.strip("'").lower() == op.privacy.lower():
            hits += 1
        elif val is None:
            total -= 1
    s.state = None if total == 0 else (hits == total)


def apply_toggles(settings: list[ToggleSetting],
                  revert: bool = False) -> tuple[bool, str]:
    """Apply (or revert) a list of ToggleSettings. Returns (ok, error_message)."""
    if not settings:
        return True, ''
    if sys.platform == 'win32':
        ok, err = _apply_windows(settings, revert)
    elif sys.platform == 'linux':
        ok, err = _apply_linux(settings, revert)
    else:
        return False, 'Toggle settings are not supported on this platform yet.'
    if ok:
        for s in settings:
            s.state = not revert
    return ok, err


def _apply_windows(settings: list[ToggleSetting], revert: bool) -> tuple[bool, str]:
    hkcu_ops:  list[RegOp]  = []
    hklm_ops:  list[RegOp]  = []
    svc_ops:   list[SvcOp]  = []
    task_ops:  list[TaskOp] = []
    bat_lines: list[str]    = []
    for s in settings:
        for op in s.reg_ops:
            (hkcu_ops if op.hive == 'HKCU' else hklm_ops).append(op)
        svc_ops.extend(s.svc_ops)
        task_ops.extend(s.task_ops)
        bat_lines.extend(s.bat_revert if revert else s.bat_apply)
    for op in hkcu_ops:
        _write_hkcu(op, revert)
    if hklm_ops or svc_ops or task_ops or bat_lines:
        return _run_elevated_batch(hklm_ops, svc_ops, task_ops, bat_lines, revert)
    return True, ''


def _apply_linux(settings: list[ToggleSetting], revert: bool) -> tuple[bool, str]:
    system_svc:  list[SystemdSvcOp] = []
    user_svc:    list[SystemdSvcOp] = []
    sysctl_ops:  list[SysctlOp]    = []
    gsettings:   list[GSettingsOp] = []
    sh_lines:    list[str]         = []

    for s in settings:
        for op in s.systemd_ops:
            (user_svc if op.user else system_svc).append(op)
        sysctl_ops.extend(s.sysctl_ops)
        gsettings.extend(s.gsettings_ops)
        sh_lines.extend(s.sh_revert if revert else s.sh_apply)

    # gsettings + user services: no elevation
    for op in gsettings:
        val = op.default if revert else op.privacy
        try:
            subprocess.run(['gsettings', 'set', op.schema, op.key, val], timeout=8)
        except Exception:
            pass

    for op in user_svc:
        _systemctl_user(op, revert)

    # system services + sysctl: need elevation
    if system_svc or sysctl_ops or sh_lines:
        return _run_linux_elevated(system_svc, sysctl_ops, sh_lines, revert)

    return True, ''


def _systemctl_user(op: SystemdSvcOp, revert: bool):
    if revert:
        cmds = [['systemctl', '--user', 'unmask', op.name],
                ['systemctl', '--user', 'enable', op.name]]
    elif op.privacy_state == 'masked':
        cmds = [['systemctl', '--user', 'mask', op.name]]
    else:
        cmds = [['systemctl', '--user', 'disable', op.name]]
    for cmd in cmds:
        try:
            subprocess.run(cmd, capture_output=True, timeout=10)
        except Exception:
            pass


_SYSCTL_CONF = '/etc/sysctl.d/99-unitool-privacy.conf'


def _run_linux_elevated(svc_ops: list[SystemdSvcOp], sysctl_ops: list[SysctlOp],
                        sh_lines: list[str], revert: bool) -> tuple[bool, str]:
    lines = ['#!/bin/bash']

    for op in svc_ops:
        if revert:
            lines += [
                f'systemctl unmask {op.name} 2>/dev/null; true',
                f'systemctl enable {op.name} 2>/dev/null; true',
            ]
        elif op.privacy_state == 'masked':
            lines.append(f'systemctl mask    {op.name} 2>/dev/null; true')
        else:
            lines.append(f'systemctl disable {op.name} 2>/dev/null; true')

    for op in sysctl_ops:
        val = op.default if revert else op.privacy
        lines.append(f'sysctl -w {op.key}={val} 2>/dev/null; true')
        if op.persist:
            lines.append(f'touch {_SYSCTL_CONF}')
            lines.append(f'sed -i "/^{op.key}/d" {_SYSCTL_CONF} 2>/dev/null; true')
            if not revert:
                lines.append(f'echo "{op.key}={val}" >> {_SYSCTL_CONF}')

    lines.extend(sh_lines)

    from . import elevation
    return elevation.run_script(
        '\n'.join(lines),
        prompt='UniTool needs administrator access to apply these privacy '
               'settings (systemd services / kernel parameters).',
    )


def _write_hkcu(op: RegOp, revert: bool):
    if not _wr:
        return
    if revert and op.default is None:
        # delete the value
        hk = _wr.HKEY_CURRENT_USER
        try:
            with _wr.OpenKey(hk, op.subkey, access=_wr.KEY_SET_VALUE) as k:
                _wr.DeleteValue(k, op.value)
        except OSError:
            pass
        return
    data  = op.default if revert else op.privacy
    vtype = _wr.REG_SZ if op.vtype == 'SZ' else _wr.REG_DWORD
    try:
        with _wr.CreateKey(_wr.HKEY_CURRENT_USER, op.subkey) as k:
            _wr.SetValueEx(k, op.value, 0, vtype, data)
    except OSError:
        pass


_SC_START = {2: 'auto', 3: 'demand', 4: 'disabled'}


def _run_elevated_batch(reg_ops: list[RegOp], svc_ops: list[SvcOp],
                        task_ops: list[TaskOp], extra_bat: list[str],
                        revert: bool) -> tuple[bool, str]:
    """Build one .bat file with all elevated commands and run it once."""
    import ctypes, ctypes.wintypes

    lines = ['@echo off', 'set RESULT=0']

    for op in reg_ops:
        if revert and op.default is None:
            lines.append(f'reg delete "{op.hive}\\{op.subkey}" /v "{op.value}" /f >nul 2>&1')
        else:
            data  = op.default if revert else op.privacy
            rtype = 'REG_SZ' if op.vtype == 'SZ' else 'REG_DWORD'
            lines.append(f'reg add "{op.hive}\\{op.subkey}" /v "{op.value}" '
                         f'/t {rtype} /d "{data}" /f >nul 2>&1')
            lines.append('if %errorlevel% neq 0 set RESULT=%errorlevel%')

    for op in svc_ops:
        start = op.default_start if revert else op.privacy_start
        lines.append(f'sc config "{op.name}" start= {_SC_START.get(start,"disabled")} >nul 2>&1')

    for op in task_ops:
        action = 'Enable' if revert else 'Disable'
        lines.append(f'schtasks /Change /{action} /TN "{op.path}" >nul 2>&1')

    lines.extend(extra_bat)
    lines.append('exit /b %RESULT%')

    fd, bat = tempfile.mkstemp(suffix='.bat', prefix='unitool_prv_')
    try:
        os.write(fd, '\r\n'.join(lines).encode('cp1252', errors='replace'))
        os.close(fd)

        try:
            is_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            is_admin = False

        if is_admin:
            r = subprocess.run(['cmd.exe', '/C', bat], capture_output=True,
                               creationflags=0x08000000)
            return r.returncode == 0, r.stderr.decode(errors='replace').strip()

        class _SEI(ctypes.Structure):
            _fields_ = [
                ('cbSize',       ctypes.wintypes.DWORD),
                ('fMask',        ctypes.c_ulong),
                ('hwnd',         ctypes.wintypes.HWND),
                ('lpVerb',       ctypes.c_wchar_p),
                ('lpFile',       ctypes.c_wchar_p),
                ('lpParameters', ctypes.c_wchar_p),
                ('lpDirectory',  ctypes.c_wchar_p),
                ('nShow',        ctypes.c_int),
                ('hInstApp',     ctypes.wintypes.HINSTANCE),
                ('lpIDList',     ctypes.c_void_p),
                ('lpClass',      ctypes.c_wchar_p),
                ('hkeyClass',    ctypes.wintypes.HKEY),
                ('dwHotKey',     ctypes.wintypes.DWORD),
                ('hIcon',        ctypes.wintypes.HANDLE),
                ('hProcess',     ctypes.wintypes.HANDLE),
            ]

        sei              = _SEI()
        sei.cbSize       = ctypes.sizeof(sei)
        sei.fMask        = 0x40
        sei.lpVerb       = 'runas'
        sei.lpFile       = 'cmd.exe'
        sei.lpParameters = f'/C "{bat}"'
        sei.nShow        = 0

        if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
            return False, 'UAC elevation was cancelled or denied.'

        ctypes.windll.kernel32.WaitForSingleObject(sei.hProcess, 0xFFFFFFFF)
        rc = ctypes.wintypes.DWORD()
        ctypes.windll.kernel32.GetExitCodeProcess(sei.hProcess, ctypes.byref(rc))
        ctypes.windll.kernel32.CloseHandle(sei.hProcess)
        return rc.value == 0, ('' if rc.value == 0 else f'Script exited with code {rc.value}')
    finally:
        try:
            os.unlink(bat)
        except OSError:
            pass
