from setuptools import setup

APP = ["autoclicker.py"]
APP_ICON = "app-icon.icns"
DATA_FILES = [('Resources', ['README.md', APP_ICON])]
OPTIONS = {
    "argv_emulation": False,
    "packages": ["pyautogui", "pynput"],
    "includes": ["ApplicationServices"],
    "excludes": ["PIL", "numpy", "cv2", "matplotlib"],
    "iconfile": APP_ICON,
    "plist": {
        "CFBundleName": "macOS autoclicker",
        "CFBundleDisplayName": "macOS autoclicker",
        "CFBundleIdentifier": "com.ashton.autoclicker",
        "CFBundleVersion": "1.0.0",
        "CFBundlePackageType": "APPL",
        "LSUIElement": False,
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,
    },
}

setup(
    name="macOS autoclicker",
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
