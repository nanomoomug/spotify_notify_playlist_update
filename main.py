# -*- coding: utf-8 -*-
import argparse
import datetime
import json
import logging
import os
import smtplib
import sqlite3
import time
import traceback
from email.mime.text import MIMEText
from typing import Any, Iterable, List, Mapping, NewType, Optional, Tuple

import daemon  # type: ignore
import dominate  # type: ignore
import requests
import spotipy  # type: ignore
from dominate.tags import (a, div, h2, h3, hr, img, span,  # type: ignore
                           table, td, tr)
from spotipy.oauth2 import SpotifyClientCredentials  # type: ignore

ConnectionId = NewType('ConnectionId', int)
ClientId = NewType('ClientId', str)
ClientSecret = NewType('ClientSecret', str)

PlaylistId = NewType('PlaylistId', int)
SpotifyPlaylistId = NewType('SpotifyPlaylistId', str)
PlayList = NewType('PlayList', Mapping[Any, Any])
Track = NewType('Track', Mapping[Any, Any])

Email = NewType('Email', str)

LOCAL_PATH = os.path.dirname(os.path.realpath(__file__))
SQLITE_FILE = os.path.join(LOCAL_PATH, 'data.db')
SQLITE_TEMPLATE_FILE = os.path.join(LOCAL_PATH, 'data-empty-template.db')


def _initialize_db() -> sqlite3.Connection:
    if not os.path.exists(SQLITE_FILE):
        logging.warn(
            'DB not found. Starting an empty DB. This program will not run '
            'correctly until the necessary information has been added to '
            'the DB'
        )
        from shutil import copyfile
        copyfile(SQLITE_TEMPLATE_FILE, SQLITE_FILE)
    logging.info(
        'DB connection to {} successfully opened'.format(SQLITE_FILE))
    return sqlite3.connect(SQLITE_FILE)


def _enumerate_connections(
        db: sqlite3.Connection
) -> Iterable[Tuple[ConnectionId, ClientId, ClientSecret]]:
    cursor = db.cursor()
    sql_select_credentials = (
        'SELECT id, client_id, client_secret FROM connection_credentials')
    for res in cursor.execute(sql_select_credentials):
        yield res


def _spotify_connection(client_id, client_secret) -> spotipy.client.Spotify:
    return spotipy.Spotify(
        client_credentials_manager=SpotifyClientCredentials(
            client_id=client_id, client_secret=client_secret
        ))


def _enumerate_connection_playlists(
        db: sqlite3.Connection,
        connection_id: ConnectionId
) -> Iterable[Tuple[PlaylistId, SpotifyPlaylistId, Optional[PlayList]]]:
    cursor = db.cursor()
    sql_select_credentials = (
        'SELECT id, spotify_playlist_id, last_state_json FROM playlists '
        'WHERE connection_id = ?')
    for id_, spotify_playlist_id, last_state_json in cursor.execute(
            sql_select_credentials, (connection_id,)):
        last_state = None
        if last_state_json is not None:
            last_state = json.loads(last_state_json)
        yield id_, spotify_playlist_id, last_state


def _get_playlist_from_spotify(
        spotify: spotipy.client.Spotify,
        spotify_playlist_id: SpotifyPlaylistId
) -> PlayList:
    return spotify.playlist(spotify_playlist_id)


def _get_new_songs(playlist, last_state):
    if last_state is None:
        return []

    format_ = '%Y-%m-%dT%H:%M:%SZ'
    max_last_state = max(
        [datetime.datetime.strptime(item['added_at'], format_)
         for item in last_state['tracks']['items']]
    )

    def compare(item):
        return (datetime.datetime.strptime(item['added_at'], format_)
                > max_last_state)
    return list(filter(compare, playlist['tracks']['items']))


def _save_playlist_to_db(
        db: sqlite3.Connection,
        playlist_id: PlaylistId,
        playlist: PlayList
) -> None:
    cursor = db.cursor()
    sql_save_playlist = (
        'UPDATE playlists SET last_state_json=? WHERE id = ?'
    )
    cursor.execute(sql_save_playlist, (json.dumps(playlist), playlist_id))
    db.commit()


def _check_for_updates(db: sqlite3.Connection) -> None:
    logging.info('Checking for updates')
    for connection_id, client_id, client_secret in _enumerate_connections(db):
        spotify = _spotify_connection(client_id, client_secret)
        for playlist_entry in _enumerate_connection_playlists(
                db, connection_id):
            playlist_id, spotify_playlist_id, last_state = playlist_entry
            playlist = _get_playlist_from_spotify(spotify, spotify_playlist_id)
            new_songs = _get_new_songs(playlist, last_state)
            _save_playlist_to_db(db, playlist_id, playlist)
            if len(new_songs) > 0:
                logging.info(
                    'The playlist {} was updated.'.format(spotify_playlist_id))
                _send_email(db, new_songs, playlist_id, playlist)
    logging.info('Finished checking for updates')


def _collect_email_addresses(
        db: sqlite3.Connection,
        playlist_id: PlaylistId
) -> List[Email]:
    cursor = db.cursor()
    sql_select_credentials = (
        'SELECT m.email FROM playlists p '
        'INNER JOIN playlist_groups pg ON p.id = pg.playlist_id '
        'INNER JOIN group_members gm ON pg.group_id = gm.group_id '
        'INNER JOIN members m ON m.id = gm.member_id '
        'WHERE p.id = ?')

    return [x[0] for x in cursor.execute(
        sql_select_credentials, (playlist_id,))]


def _playlist_name(playlist: PlayList):
    return playlist['name']


def _generate_html_email_body(
        title: str,
        new_songs: Iterable[Track],
        playlist: PlayList
) -> str:
    link_style = 'color: #373737;'

    # Generate links to the artists
    artists_spans = []
    for track in new_songs:
        artists = []
        for artist in track['track']['artists']:
            name = artist['name']
            url = '#'
            if ('external_urls' in artist
                    and 'spotify' in artist['external_urls']):
                url = artist['external_urls']['spotify']
                artists.append(a(name, href=url, style=link_style))
            else:
                artists.append(name)
        first = True
        artists_span = span()
        for artist_entry in artists:
            if not first:
                artists_span += ', '
            artists_span += artist_entry
            first = False
        artists_spans.append(artists_span)

    # Generate document
    doc = dominate.document(title=title)
    with doc:
        with div(style=('font-family: arial, serif;border: 1px solid #99ff99; '
                        'padding: 3px; width: 600px; margin: auto; '
                        'background-color: #99ff99;')):
            with table():
                with tr():
                    with td():
                        with a(href=playlist['external_urls']['spotify']):
                            img(src=playlist['images'][1]['url'],
                                width='200px', height='200px')
                    with td(style=('vertical-align: text-top; '
                                   'padding-left: 10px;')):
                        div('New music was added to',
                            style='text-align: center;')
                        with h2():
                            a(playlist['name'],
                              href=playlist['external_urls']['spotify'],
                              style=link_style)
                        h3(playlist['description'],
                           style='text-align: center;')
            hr(size='1', color='black', width='90%')
            div('The following tracks where added:',
                style='margin-top: 10px; margin-bottom: 10px;')

            for track, artists_span in zip(new_songs, artists_spans):
                with table(style=('border: 1px solid black; width:100%; '
                                  'margin-bottom: 10px; font-size: 14px;')):
                    with td(width='100px;', style='width: 100px;'):
                        a(img(src=track['track']['album']['images'][2]['url']),
                          href=track['track']['external_urls']['spotify'])
                    with td():
                        with table():
                            with tr():
                                td('Artist(s):',
                                   style=('text-align: right; '
                                          'padding-right: 5px;'))
                                td(artists_span)
                            with tr():
                                td('Title:',
                                   style='text-align: right; '
                                   'padding-right: 5px;')
                                td(a(track['track']['name'],
                                     href=track['track']['external_urls'][
                                         'spotify'],
                                     style=link_style))
                            with tr():
                                td('Album:',
                                   style='text-align: right; '
                                   'padding-right: 5px;')
                                td(a(track['track']['album']['name'],
                                     href=track['track']['album'][
                                         'external_urls']['spotify'],
                                     style=link_style))

    return doc.render(pretty=False)


def _send_email(
        db: sqlite3.Connection,
        new_songs: Iterable[Track],
        playlist_id: PlaylistId,
        playlist: PlayList
) -> None:
    cursor = db.cursor()
    try:
        response = cursor.execute(
            'SELECT email_sender, email_host, email_port, email_password '
            'FROM global_config'
        ).fetchone()
        if response is not None:
            sender, host, port, password = response
        else:
            logging.error(
                'No information about email sending in the DB!')
            return
    except Exception:
        logging.error(
            'Could not read information from DB required to send emails.')
        traceback.print_exc()
        return

    receivers = _collect_email_addresses(db, playlist_id)

    subject = 'Update to the playlist "{}"'.format(_playlist_name(playlist))

    body_of_email = _generate_html_email_body(subject, new_songs, playlist)

    msg = MIMEText(body_of_email, 'html')
    msg['Subject'] = subject
    msg['From'] = sender
    msg['To'] = ', '.join(receivers)

    smpt_conn = smtplib.SMTP_SSL(host=host, port=port)
    smpt_conn.login(user=sender, password=password)
    smpt_conn.sendmail(sender, receivers, msg.as_string())
    smpt_conn.quit()


def main(sleep_time: int) -> None:
    logging.info('Starting Spotify playlist update notification program')

    db = _initialize_db()

    while True:
        try:
            _check_for_updates(db)
            time.sleep(sleep_time)
        except requests.exceptions.ConnectionError:
            logging.error(
                ('Failed to connect. This might be because there is no '
                 'internet. Retrying in one minute.'), exc_info=True)
            time.sleep(60)
        except Exception:
            logging.error(
                ('Exception while checking for update. Waiting 10 minutes and '
                 'retrying.'), exc_info=True)
            time.sleep(10*60)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Notify by email when playlists change')
    parser.add_argument(
        '--daemon', dest='daemon', action='store_const', const=True,
        default=False, help='Start as daemon and log to journald')
    parser.add_argument('-s', '--sleep', dest='sleep', type=int,
                        default=60*60,  # 1 hour
                        help=('How long to wait between checks.'))

    args = parser.parse_args()
    if args.daemon:
        from systemd import journal  # type: ignore
        with daemon.DaemonContext(working_directory=LOCAL_PATH):
            logging.basicConfig(level=logging.INFO,
                                handlers=[journal.JournaldLogHandler()])
            logging.info('Starting as a daemon')
            main(args.sleep)
    else:
        logging.basicConfig(level=logging.INFO)
        main(args.sleep)
