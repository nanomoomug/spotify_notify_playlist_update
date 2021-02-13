#!/bin/bash -e
if [ "$#" -ne 1 ]; then
    echo "A python executable needs to be passed as an argument"
	exit 1
fi

if [[ -x "$1" ]]
then
    echo "File '$1' is an executable"
else
    echo "File '$1' is not executable or found"
	exit 2
fi

echo "Creating a virtual environment"
$1 -m venv venv
echo "Virtual environment created"

echo "Installing required libraries"
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
echo "Libraries installed"

echo "Program can now be configured in systemd. See the template 'spotify_notify_playlist_update.service' in the repository."
echo "The python executable to be used for the systemd configuration is '$(realpath -s venv/bin/python)'"
