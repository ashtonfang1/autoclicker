#!/usr/bin/env zsh
# Build the macOS autoclicker .app bundle with py2app.
#
# py2app 0.28.10 + modulegraph 0.19.7 have a known AST-walker bug on
# Python 3.13, and the `rubicon` namespace package ships without an
# __init__.py, so py2app's legacy `imp.find_module` can't locate it.
# The one-liner below adds a PEP 420-style namespace __init__.py so the
# build works. It is idempotent and only touches the installed package.
set -e

APP_PATH="dist/macOS autoclicker.app"
RUBICON_DIR="$(python3 -c 'import rubicon.objc, os; print(os.path.dirname(os.path.dirname(rubicon.objc.__file__)))')"
if [ ! -f "$RUBICON_DIR/__init__.py" ]; then
    printf '__path__ = __import__("pkgutil").extend_path(__path__, __name__)\n' > "$RUBICON_DIR/__init__.py"
fi

CODESIGN_IDENTITY="${CODESIGN_IDENTITY:-LocalDevSign}"
if [ "$CODESIGN_IDENTITY" = "LocalDevSign" ]; then
    if ! SIGNING_IDENTITY=$(security find-identity -v -p codesigning |
        awk '$0 ~ /"LocalDevSign"$/ { print $2; matches++ }
             END { if (matches != 1) exit 1 }'); then
        echo "ERROR: no valid LocalDevSign code-signing identity was found." >&2
        echo "Create a valid Code Signing identity with its private key, or explicitly set CODESIGN_IDENTITY." >&2
        echo "Use CODESIGN_IDENTITY=- only to opt in to an ad-hoc build." >&2
        exit 1
    fi
else
    SIGNING_IDENTITY="$CODESIGN_IDENTITY"
fi
if [ "$SIGNING_IDENTITY" = "-" ]; then
    echo "WARNING: signing ad-hoc; macOS permissions may need to be granted again after rebuilds."
else
    echo "Signing app with: $SIGNING_IDENTITY"
fi

rm -rf build dist
python3 setup.py py2app
if [ ! -d "$APP_PATH" ]; then
    echo "ERROR: py2app did not create $APP_PATH" >&2
    exit 1
fi
ACTUAL_BUNDLE_ID=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' \
    "$APP_PATH/Contents/Info.plist")
if [ "$ACTUAL_BUNDLE_ID" != "com.ashton.autoclicker" ]; then
    echo "ERROR: unexpected bundle identifier: $ACTUAL_BUNDLE_ID" >&2
    exit 1
fi
for code_dir in "$APP_PATH/Contents/Frameworks" "$APP_PATH/Contents/Resources"; do
    if [ -d "$code_dir" ]; then
        find "$code_dir" -type f \( -name "*.dylib" -o -name "*.so" \) \
            -exec codesign --force --sign "$SIGNING_IDENTITY" {} \;
    fi
done

find "$APP_PATH/Contents/Frameworks" -depth -type d -name "*.framework" \
    -exec codesign --force --sign "$SIGNING_IDENTITY" {} \;

if [ -f "$APP_PATH/Contents/MacOS/python" ]; then
    codesign --force --sign "$SIGNING_IDENTITY" \
        "$APP_PATH/Contents/MacOS/python"
fi

codesign --force --sign "$SIGNING_IDENTITY" \
    --identifier "com.ashton.autoclicker" "$APP_PATH"
codesign --verify --deep --strict --verbose=2 "$APP_PATH"
echo "Built: $APP_PATH"