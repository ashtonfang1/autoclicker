from setuptools import setup

APP = ["autoclicker.py"]
DATA_FILES = []
OPTIONS = {
    "argv_emulation": False,
    "packages": ["pyautogui", "pynput"],
    "excludes": ["PIL", "numpy", "cv2", "matplotlib"],
    "plist": {
        "CFBundleName": "Autoclicker",
        "CFBundleDisplayName": "Autoclicker",
        "CFBundleIdentifier": "com.tinyman.autoclicker",
        "CFBundleVersion": "1.0.0",
        "CFBundlePackageType": "APPL",
        "NSHighResolutionCapable": True,
        "NSRequiresAquaSystemAppearance": False,
    },
}

setup(
    app=APP,
    data_files=DATA_FILES,
    options={"py2app": OPTIONS},
    setup_requires=["py2app"],
)
