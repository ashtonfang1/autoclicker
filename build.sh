#!/usr/bin/env zsh
# Build the Autoclicker .app bundle with py2app.
#
# py2app 0.28.10 + modulegraph 0.19.7 have a known AST-walker bug on
# Python 3.13, and the `rubicon` namespace package ships without an
# __init__.py, so py2app's legacy `imp.find_module` can't locate it.
# The one-liner below adds a PEP 420-style namespace __init__.py so the
# build works. It is idempotent and only touches the installed package.
set -e

RUBICON_DIR="$(python3 -c 'import rubicon.objc, os; print(os.path.dirname(os.path.dirname(rubicon.objc.__file__)))')"
if [ ! -f "$RUBICON_DIR/__init__.py" ]; then
    printf '__path__ = __import__("pkgutil").extend_path(__path__, __name__)\n' > "$RUBICON_DIR/__init__.py"
fi

rm -rf build dist
python3 setup.py py2app
echo "Built: dist/Autoclicker.app"