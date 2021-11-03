#!/usr/bin/env python3

import datetime
import logging
import os
import re
import threading
import multiprocessing

import dateutil.parser
import psycopg2
import psycopg2.extras

from flask import Flask, request, jsonify
from jira_patch import JIRA, CustomFieldType, CustomFieldSearcherKey
from pepper_patch import Pepper


# Salt connection settings
SALT_URL = os.getenv('SALT_URL', 'http://salt:8000')
SALT_EAUTH = os.getenv('SALT_EAUTH', 'auto')
SALT_USERNAME = os.getenv('SALT_USERNAME', 'integration')
SALT_PASSWORD = os.getenv('SALT_PASSWORD', 'integration')

# PostgreSQL connection settings
POSTGRES_HOST = os.getenv('POSTGRES_HOST', 'postgres')
POSTGRES_PORT = int(os.getenv('POSTGRES_PORT', '5432'))
POSTGRES_USERNAME = os.getenv('POSTGRES_USERNAME', 'postgres')
POSTGRES_PASSWORD = os.getenv('POSTGRES_PASSWORD', 'postgres')
POSTGRES_DB = os.getenv('POSTGRES_DB', 'integration')
POSTGRES_AUTH = {
    'host': POSTGRES_HOST,
    'port': POSTGRES_PORT,
    'user': POSTGRES_USERNAME,
    'password': POSTGRES_PASSWORD,
    'dbname': POSTGRES_DB,
}

# Jira connection settings
JIRA_HOST = os.getenv('JIRA_HOST', 'https://jira.atlassian.com')
JIRA_USERNAME = os.getenv('JIRA_USERNAME', 'jira')
JIRA_PASSWORD = os.getenv('JIRA_PASSWORD', 'jira')

# Jira field settings
JIRA_ALL_MINIONS_FIELD = 'Minions'
JIRA_LINUX_MINIONS_FIELD = 'Linux Minions'
JIRA_WINDOWS_MINIONS_FIELD = 'Windows Minions'
JIRA_LINUX_PACKAGE_FIELD = 'Linux Package'
JIRA_WINDOWS_PACKAGE_FIELD = 'Windows Package'

# Hand-crafted list of Chocolatey packages
CHOCOLATEY_PACKAGES = [
    ('adobereader', '2021.007.20099'),
    ('adobereader', '2021.007.20095'),
    ('googlechrome', '95.0.4638.69'),
    ('googlechrome', '94.0.4606.81'),
    ('firefox', '93.0.0.20211014'),
    ('firefox', '92.0.1'),
    ('jre8', '8.0.311'),
    ('jre8', '8.0.301'),
]

# PostgreSQL queries
SELECT_INSTALL_PACKAGES_QUERY = (
    'SELECT minion_id '
    'FROM install_packages '
    'WHERE itsm_id = %s'
)

INSERT_INSTALL_PACKAGES_QUERY = (
    'INSERT INTO install_packages '
    '(itsm_id, minion_id, package_name, package_version, after) '
    'VALUES (%s, %s, %s, %s, %s) '
    'ON CONFLICT (itsm_id, minion_id, package_name, package_version) '
    'DO UPDATE SET after = EXCLUDED.after'
)

UPDATE_INSTALL_PACKAGES_QUERY = (
    'UPDATE install_packages '
    'SET reverted = TRUE '
    'WHERE itsm_id = %s'
)

INSERT_REBOOT_REQUESTS_QUERY = (
    'INSERT INTO reboot_requests '
    '(itsm_id, minion_id, job_id) '
    'VALUES (%s, %s, %s)'
)

SELECT_MINIONS_QUERY = (
    'SELECT minion_id '
    'FROM minions '
    'WHERE operating_system = %s'
)

INSERT_MINIONS_QUERY = (
    'INSERT INTO minions '
    '(minion_id, operating_system) '
    'VALUES (%s, %s) '
    'ON CONFLICT (minion_id) '
    'DO UPDATE SET operating_system = EXCLUDED.operating_system, last_seen = NOW()'
)

SELECT_AVAILABLE_PACKAGES_QUERY = (
    'SELECT package_name, package_version '
    'FROM available_packages '
    'WHERE operating_system = %s'
)

INSERT_AVAILABLE_PACKAGES_QUERY = (
    'INSERT INTO available_packages '
    '(operating_system, package_name, package_version) '
    'VALUES (%s, %s, %s)'
    'ON CONFLICT (operating_system, package_name, package_version) '
    'DO NOTHING'
)

# Regular expressions
RE_SPLIT_VERSION = re.compile(r'[\.\-\+\~\:]+')

# Multiprocess locks
SYNC_LOCK = multiprocessing.Lock()

# Logging settings
logging.basicConfig(level=logging.INFO)
log = logging.getLogger('Integration')

# Flask settings
app = Flask('Integration')


@app.route('/install', methods=['POST'])
def install():
    # Get request body
    body = request.get_json(force=True, silent=True)
    if body is None:
        return jsonify({'success': False, 'error': 'Expected a valid JSON request body.'}), 400

    try:
        # Get request values
        itsm_id = body.get('itsm_id')
        minion_ids = body.get('minion_ids', body.get('minion_id', []))
        minion_ids = list(set([minion_ids] if isinstance(minion_ids, str) else minion_ids))
        package_name = body.get('package_name')
        package_version = body.get('package_version')
        after = body.get('after')
        after = isoparse(after) if after else datetime.datetime.now()

         # Validate request values
        if not itsm_id:
            return jsonify({'success': False, 'error': 'Expected ITSM ID in field \'itsm_id\'.'}), 400
        if not minion_ids:
            return jsonify({'success': False, 'error': 'Expected list of minion IDs in field \'minion_ids\'.'}), 400
        if not package_name:
            return jsonify({'success': False, 'error': 'Expected package name in field \'package_name\'.'}), 400
        if not package_version:
            return jsonify({'success': False, 'error': 'Expected package version in field \'package_version\'.'}), 400
        if not after:
            return jsonify({'success': False, 'error': 'Expected ISO-formatted datetime in field \'after\'.'}), 400
        if package_version.lower() in ('remove', 'latest'):
            package_version = package_version.lower()
        package_version = package_version if package_version != 'remove' else None
    except:
        return jsonify({'success': False, 'error': 'Invalid parameters.'}), 400

    log.info('Install:%s: Received request to install package %s version %s on %s minions.',
             itsm_id, package_name, package_version, len(minion_ids))

    # Transition issue status to waiting on Jira
    log.info('Install:%s: Transitioning Jira issue status to waiting.', itsm_id)
    try:
        jira = JIRA(JIRA_HOST, basic_auth=(JIRA_USERNAME, JIRA_PASSWORD))
        jira.transition_issue(itsm_id, 'Wait')
    except:
        log.error('Install:%s: Failed to transition issue status on Jira.', itsm_id, exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to transition issue status on Jira.'}), 500

    # Insert data into the database
    successes, failures = {}, []
    log.info('Install:%s: Inserting package management request into the database.', itsm_id)
    try:
        with psycopg2.connect(**POSTGRES_AUTH) as connection:
            for minion_id in minion_ids:
                try:
                    with connection.cursor() as cursor:
                        values = (itsm_id, minion_id, package_name, package_version, after)
                        cursor.execute(INSERT_INSTALL_PACKAGES_QUERY, values)
                except:
                    log.error('Install:%s: Failed to insert package management request for %s into the database.',
                              itsm_id, minion_id, exc_info=True)
                    failures.append(minion_id)
    except:
        log.error('Install:%s: Failed to communicate with the database.', itsm_id, exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to communicate with the database.'}), 500

    # Connect to the Salt master using Pepper
    log.info('Install:%s: Connecting to the Salt master.', itsm_id)
    try:
        pepper = Pepper(SALT_URL)
        pepper.login(SALT_USERNAME, SALT_PASSWORD, SALT_EAUTH)
    except:
        log.error('Install:%s: Failed to connect to the Salt master.', itsm_id, exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to connect to the Salt master.'}), 500

    # Run install packages job
    log.info('Install:%s: Requesting package management job from the Salt master.', itsm_id)
    for minion_id in minion_ids:
        if minion_id in failures:
            continue
        try:
            result = pepper.local_async(minion_id, 'state.apply', ('install_packages',))
            if not result['return'][0]:
                log.error('Install:%s: Empty response when requesting package management job for %s.',
                          itsm_id, minion_id, exc_info=True)
                failures.append(minion_id)
                continue
            successes[minion_id] = result['return'][0]['jid']
        except:
            log.error('Install:%s: Failed to request package management job for %s.',
                      itsm_id, minion_id, exc_info=True)
            failures.append(minion_id)
            continue

    # Send response if there are any failures
    if failures:
        log.info('Install:%s: Finished with %s successes and %s failures.', itsm_id, len(successes), len(failures))
        return jsonify_clear({'success': False, 'successes': successes, 'failures': failures}), 500

    # Transition issue status to completed on Jira
    log.info('Install:%s: Transitioning Jira issue status to completed.', itsm_id)
    try:
        jira = JIRA(JIRA_HOST, basic_auth=(JIRA_USERNAME, JIRA_PASSWORD))
        jira.transition_issue(itsm_id, 'Complete')
    except:
        log.error('Install:%s: Failed to transition issue status on Jira.', itsm_id, exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to transition issue status on Jira.'}), 500

    # Send success response
    log.info('Install:%s: Finished with %s successes and %s failures.', itsm_id, len(successes), len(failures))
    return jsonify_clear({'success': True, 'successes': successes, 'failures': failures})


@app.route('/remove', methods=['POST'])
def remove():
    # Get request body
    body = request.get_json(force=True, silent=True)
    if body is None:
        return jsonify({'success': False, 'error': 'Expected a valid JSON request body.'}), 400

    # Get request values
    try:
        itsm_id = body.get('itsm_id')
        minion_ids = body.get('minion_ids', body.get('minion_id', []))
        minion_ids = list(set([minion_ids] if isinstance(minion_ids, str) else minion_ids))
        package_name = body.get('package_name')
        after = body.get('after')
        after = isoparse(after) if after else datetime.datetime.now()

        # Validate request values
        if not itsm_id:
            return jsonify({'success': False, 'error': 'Expected ITSM ID in field \'itsm_id\'.'}), 400
        if not minion_ids:
            return jsonify({'success': False, 'error': 'Expected list of minion IDs in field \'minion_ids\'.'}), 400
        if not package_name:
            return jsonify({'success': False, 'error': 'Expected package name in field \'package_name\'.'}), 400
        if not after:
            return jsonify({'success': False, 'error': 'Expected ISO-formatted datetime in field \'after\'.'}), 400
    except:
        return jsonify({'success': False, 'error': 'Invalid parameters.'}), 400

    log.info('Remove:%s: Received request to remove package %s on %s minions.',
             itsm_id, package_name, len(minion_ids))

    # Transition issue status to waiting on Jira
    log.info('Remove:%s: Transitioning Jira issue status to waiting.', itsm_id)
    try:
        jira = JIRA(JIRA_HOST, basic_auth=(JIRA_USERNAME, JIRA_PASSWORD))
        jira.transition_issue(itsm_id, 'Wait')
    except:
        log.error('Remove:%s: Failed to transition issue status on Jira.', itsm_id, exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to transition issue status on Jira.'}), 500

    # Insert data into the database
    successes, failures = {}, []
    log.info('Remove:%s: Inserting package management request into the database.', itsm_id)
    try:
        with psycopg2.connect(**POSTGRES_AUTH) as connection:
            for minion_id in minion_ids:
                try:
                    with connection.cursor() as cursor:
                        values = (itsm_id, minion_id, package_name, None, after)
                        cursor.execute(INSERT_INSTALL_PACKAGES_QUERY, values)
                except:
                    log.error('Remove:%s: Failed to insert package management request for %s into the database.',
                              itsm_id, minion_id, exc_info=True)
                    failures.append(minion_id)
    except:
        log.error('Remove:%s: Failed to communicate with the database.', itsm_id, exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to communicate with the database.'}), 500

    # Connect to the Salt master using Pepper
    log.info('Remove:%s: Connecting to the Salt master.', itsm_id)
    try:
        pepper = Pepper(SALT_URL)
        pepper.login(SALT_USERNAME, SALT_PASSWORD, SALT_EAUTH)
    except:
        log.error('Remove:%s: Failed to connect to the Salt master.', itsm_id, exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to connect to the Salt master.'}), 500

    # Run install packages job
    log.info('Remove:%s: Requesting package management job from the Salt master.', itsm_id)
    for minion_id in minion_ids:
        if minion_id in failures:
            continue
        try:
            result = pepper.local_async(minion_id, 'state.apply', ('install_packages',))
            if not result['return'][0]:
                log.error('Remove:%s: Empty response when requesting package management job for %s.',
                          itsm_id, minion_id, exc_info=True)
                failures.append(minion_id)
                continue
            successes[minion_id] = result['return'][0]['jid']
        except:
            log.error('Remove:%s: Failed to request package management job for %s.',
                      itsm_id, minion_id, exc_info=True)
            failures.append(minion_id)
            continue

    # Send response if there are any failures
    if failures:
        log.info('Remove:%s: Finished with %s successes and %s failures.', itsm_id, len(successes), len(failures))
        return jsonify_clear({'success': False, 'successes': successes, 'failures': failures}), 500

    # Transition issue status to completed on Jira
    log.info('Remove:%s: Transitioning Jira issue status to completed.', itsm_id)
    try:
        jira = JIRA(JIRA_HOST, basic_auth=(JIRA_USERNAME, JIRA_PASSWORD))
        jira.transition_issue(itsm_id, 'Complete')
    except:
        log.error('Remove:%s: Failed to transition issue status on Jira.', itsm_id, exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to transition issue status on Jira.'}), 500

    # Send success response
    log.info('Remove:%s: Finished with %s successes and %s failures.', itsm_id, len(successes), len(failures))
    return jsonify_clear({'success': True, 'successes': successes, 'failures': failures})


@app.route('/revert', methods=['POST'])
def revert():
    # Get request body
    body = request.get_json(force=True, silent=True)
    if body is None:
        return jsonify({'success': False, 'error': 'Expected a valid JSON request body.'}), 400

    try:
        # Get request values
        itsm_id = body.get('itsm_id')

        # Validate request values
        if not itsm_id:
            return jsonify({'success': False, 'error': 'Expected ITSM ID in field \'itsm_id\'.'}), 400
    except:
        return jsonify({'success': False, 'error': 'Invalid parameters.'}), 400

    log.info('Revert:%s: Received request to revert issue %s.', itsm_id, itsm_id)

    # Transition issue status to waiting on Jira
    log.info('Revert:%s: Transitioning Jira issue status to waiting.', itsm_id)
    try:
        jira = JIRA(JIRA_HOST, basic_auth=(JIRA_USERNAME, JIRA_PASSWORD))
        jira.transition_issue(itsm_id, 'Wait')
    except:
        log.error('Revert:%s: Failed to transition issue status on Jira.', itsm_id, exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to transition issue status on Jira.'}), 500

    # Update data in the database
    log.info('Revert:%s: Inserting package management request into the database.', itsm_id)
    try:
        with psycopg2.connect(**POSTGRES_AUTH) as connection:
            try:
                with connection.cursor() as cursor:
                    cursor.execute(UPDATE_INSTALL_PACKAGES_QUERY, (itsm_id,))
                connection.commit()
                with connection.cursor() as cursor:
                    cursor.execute(SELECT_INSTALL_PACKAGES_QUERY, (itsm_id,))
                    minion_ids = list(set([row[0] for row in cursor]))
            except:
                log.error('Revert:%s: Failed to insert package management request into the database.',
                          itsm_id, exc_info=True)
                return jsonify({'success': False, 'error': 'Failed to communicate with the database.'}), 500
    except:
        log.error('Revert:%s: Failed to communicate with the database.', itsm_id, exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to communicate with the database.'}), 500

    # Connect to the Salt master using Pepper
    log.info('Revert:%s: Connecting to the Salt master.', itsm_id)
    try:
        pepper = Pepper(SALT_URL)
        pepper.login(SALT_USERNAME, SALT_PASSWORD, SALT_EAUTH)
    except:
        log.error('Revert:%s: Failed to connect to the Salt master.', itsm_id, exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to connect to the Salt master.'}), 500

    # Run install packages job
    log.info('Revert:%s: Requesting package management job from the Salt master.', itsm_id)
    successes, failures = {}, []
    for minion_id in minion_ids:
        try:
            result = pepper.local_async(minion_id, 'state.apply', ('install_packages',))
            if not result['return'][0]:
                log.error('Revert:%s: Empty response when requesting package management job for %s.',
                          itsm_id, minion_id, exc_info=True)
                failures.append(minion_id)
                continue
            successes[minion_id] = result['return'][0]['jid']
        except:
            log.error('Revert:%s: Failed to request package management job for %s.',
                      itsm_id, minion_id, exc_info=True)
            failures.append(minion_id)
            continue

    # Send response if there are any failures
    if failures:
        log.info('Revert:%s: Finished with %s successes and %s failures.', itsm_id, len(successes), len(failures))
        return jsonify_clear({'success': False, 'successes': successes, 'failures': failures}), 500

    # Transition issue status to completed on Jira
    log.info('Revert:%s: Transitioning Jira issue status to completed.', itsm_id)
    try:
        jira = JIRA(JIRA_HOST, basic_auth=(JIRA_USERNAME, JIRA_PASSWORD))
        jira.transition_issue(itsm_id, 'Complete')
    except:
        log.error('Revert:%s: Failed to transition issue status on Jira.', itsm_id, exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to transition issue status on Jira.'}), 500

    # Send success response
    log.info('Revert:%s: Finished with %s successes and %s failures.', itsm_id, len(successes), len(failures))
    return jsonify_clear({'success': True, 'successes': successes, 'failures': failures})


@app.route('/reboot', methods=['POST'])
def reboot():
    # Get request body
    body = request.get_json(force=True, silent=True)
    if body is None:
        return jsonify({'success': False, 'error': 'Expected a valid JSON request body.'}), 400

    try:
        # Get request values
        itsm_id = body.get('itsm_id')
        minion_ids = body.get('minion_ids', body.get('minion_id', []))
        minion_ids = list(set([minion_ids] if isinstance(minion_ids, str) else minion_ids))

        # Validate request values
        if not itsm_id:
            return jsonify({'success': False, 'error': 'Expected ITSM ID in field \'itsm_id\'.'}), 400
        if not minion_ids:
            return jsonify({'success': False, 'error': 'Expected list of minion IDs in field \'minion_ids\'.'}), 400
    except:
        return jsonify({'success': False, 'error': 'Invalid parameters.'}), 400

    log.info('Reboot:%s: Received request to reboot %s minions.', itsm_id, len(minion_ids))

    # Transition issue status to waiting on Jira
    log.info('Reboot:%s: Transitioning Jira issue status to waiting.', itsm_id)
    try:
        jira = JIRA(JIRA_HOST, basic_auth=(JIRA_USERNAME, JIRA_PASSWORD))
        jira.transition_issue(itsm_id, 'Wait')
    except:
        log.error('Reboot:%s: Failed to transition issue status on Jira.', itsm_id, exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to transition issue status on Jira.'}), 500

    # Connect to the Salt master using Pepper
    log.info('Reboot:%s: Connecting to the Salt master.', itsm_id)
    try:
        pepper = Pepper(SALT_URL)
        pepper.login(SALT_USERNAME, SALT_PASSWORD, SALT_EAUTH)
    except:
        log.error('Reboot:%s: Failed to connect to the Salt master.', itsm_id, exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to connect to the Salt master.'}), 500

    # Run reboot job
    log.info('Reboot:%s: Requesting reboot job from the Salt master.', itsm_id)
    successes, failures = {}, []
    job_ids = {}
    for minion_id in minion_ids:
        try:
            result = pepper.local_async(minion_id, 'system.reboot', (0,))
            job_ids[minion_id] = result['return'][0]['jid']
        except:
            log.error('Reboot:%s: Failed to request reboot job for %s.', itsm_id, minion_id, exc_info=True)
            failures.append(minion_id)

    # Insert request data in the database
    log.info('Reboot:%s: Inserting reboot request into the database.', itsm_id)
    try:
        with psycopg2.connect(**POSTGRES_AUTH) as connection:
            for minion_id, job_id in job_ids.items():
                try:
                    with connection.cursor() as cursor:
                        cursor.execute(INSERT_REBOOT_REQUESTS_QUERY, (itsm_id, minion_id, job_id))
                    successes[minion_id] = job_id
                except:
                    log.error('Reboot:%s: Failed to insert reboot request for %s into the database.',
                              itsm_id, minion_id, exc_info=True)
                    failures.append(minion_id)
    except:
        log.error('Reboot:%s: Failed to communicate with the database.', itsm_id, exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to communicate with the database.'}), 500

    # Send response if there are any failures
    if failures:
        log.info('Reboot:%s: Finished with %s successes and %s failures.', itsm_id, len(successes), len(failures))
        return jsonify_clear({'success': False, 'successes': successes, 'failures': failures}), 500

    # Transition issue status to completed on Jira
    log.info('Reboot:%s: Transitioning Jira issue status to completed.', itsm_id)
    try:
        jira = JIRA(JIRA_HOST, basic_auth=(JIRA_USERNAME, JIRA_PASSWORD))
        jira.transition_issue(itsm_id, 'Complete')
    except:
        log.error('Reboot:%s: Failed to transition issue status on Jira.', itsm_id, exc_info=True)
        return jsonify({'success': False, 'error': 'Failed to transition issue status on Jira.'}), 500

    # Send success response
    log.info('Reboot:%s: Finished with %s successes and %s failures.', itsm_id, len(successes), len(failures))
    return jsonify_clear({'success': True, 'successes': successes, 'failures': failures})


@app.route('/sync', methods=['POST'])
def sync():
    thread = threading.Thread(target=sync_data, daemon=True)
    thread.start()
    return jsonify({'success': True})


def sync_data():
    with SYNC_LOCK:
        log.info('Sync: Received request to sync data with Jira.')

        try:
            pepper = Pepper(SALT_URL)
            pepper.login(SALT_USERNAME, SALT_PASSWORD, SALT_EAUTH)
        except:
            log.error('Sync: Failed to connect to the Salt master.', exc_info=True)
            return False

        log.info('Sync: Requesting list of minions and packages from the Salt master.')
        linux_return_data, windows_return_data = [], []
        try:
            kwarg = {'all_versions': True}
            linux_result = pepper.local('kernel:Linux', 'pkg.list_repo_pkgs', tgt_type='grain')
            linux_return_data.extend(linux_result['return'])
            pepper.local('kernel:Windows', 'state.apply', ('install_chocolatey',), tgt_type='grain')
            windows_result = pepper.local('kernel:Windows', 'chocolatey.list', kwarg=kwarg, tgt_type='grain')
            windows_return_data.extend(windows_result['return'])
        except:
            log.error('Sync: Failed to fetch available packages from the Salt master.', exc_info=True)
            return False

        log.info('Sync: Preparing data to be inserted.')
        minion_ids = set()
        available_packages = []
        for data in linux_return_data:
            for minion_id, packages in data.items():
                if not isinstance(packages, dict):
                    continue
                minion_ids.add((minion_id, 'Linux'))
                for package, versions in packages.items():
                    blacklisted = (
                        package.startswith('linux-') or
                        package.endswith('-dev') or
                        package.endswith('-dbg') or
                        package.endswith('-doc')
                    )
                    if blacklisted:
                        continue
                    for version in versions:
                        if version != '(null)':
                            available_packages.append(('Linux', package, version))
        for data in windows_return_data:
            for minion_id, packages in data.items():
                if not isinstance(packages, dict):
                    continue
                minion_ids.add((minion_id, 'Windows'))
                for package, versions in packages.items():
                    for version in versions:
                        if version != '(null)':
                            available_packages.append(('Windows', package, version))
        available_packages += [('Windows', package, version) for package, version in CHOCOLATEY_PACKAGES]
        minion_ids = [(minion_id, operating_system) for minion_id, operating_system in minion_ids]

        log.info('Sync: Inserting new data into the database.')
        try:
            with psycopg2.connect(**POSTGRES_AUTH) as connection:
                with connection.cursor() as cursor:
                    psycopg2.extras.execute_batch(cursor, INSERT_MINIONS_QUERY, minion_ids)
                with connection.cursor() as cursor:
                    psycopg2.extras.execute_batch(cursor, INSERT_AVAILABLE_PACKAGES_QUERY, available_packages)
        except:
            log.error('Failed to communicate with the database.', exc_info=True)
            return False

        log.info('Sync: Reading all data from the database.')
        linux_minions, windows_minions = [], []
        linux_packages, windows_packages = {}, {}
        linux_packages_total = windows_packages_total = 0
        try:
            with psycopg2.connect(**POSTGRES_AUTH) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(SELECT_MINIONS_QUERY, ('Linux',))
                    linux_minions = [row[0] for row in cursor]
                with connection.cursor() as cursor:
                    cursor.execute(SELECT_MINIONS_QUERY, ('Windows',))
                    windows_minions = [row[0] for row in cursor]
                with connection.cursor() as cursor:
                    cursor.execute(SELECT_AVAILABLE_PACKAGES_QUERY, ('Linux',))
                    for package_name, package_version in cursor:
                        to_add = 1 if package_name in linux_packages else 3
                        if linux_packages_total + to_add >= JIRA.FIELD_OPTIONS_LIMIT:
                            break
                        linux_packages_total += to_add
                        linux_packages.setdefault(package_name, []).append(package_version)
                with connection.cursor() as cursor:
                    cursor.execute(SELECT_AVAILABLE_PACKAGES_QUERY, ('Windows',))
                    for package_name, package_version in cursor:
                        to_add = 1 if package_name in windows_packages else 3
                        if windows_packages_total + to_add >= JIRA.FIELD_OPTIONS_LIMIT:
                            break
                        windows_packages_total += to_add
                        windows_packages.setdefault(package_name, []).append(package_version)
        except:
            log.error('Sync: Failed to communicate with the database.', exc_info=True)
            return False

        log.info('Sync: Preparing data to be sent to Jira.')
        all_minions = sorted(linux_minions + windows_minions)
        linux_minions = sorted(linux_minions)
        windows_minions = sorted(windows_minions)
        for package_name, package_versions in linux_packages.items():
            tail = sorted(package_versions, key=split_version, reverse=True)
            linux_packages[package_name] = ['Remove'] + [version for version in tail if version.lower() != 'remove']
        for package_name, package_versions in windows_packages.items():
            tail = sorted(package_versions, key=split_version, reverse=True)
            windows_packages[package_name] = ['Remove'] + [version for version in tail if version.lower() != 'remove']
        linux_packages = dict(sorted(linux_packages.items(), key=lambda item: item[0]))
        windows_packages = dict(sorted(windows_packages.items(), key=lambda item: item[0]))

        try:
            log.info('Sync: Getting custom fields from Jira.')
            jira = JIRA(JIRA_HOST, basic_auth=(JIRA_USERNAME, JIRA_PASSWORD))
            fields = {field['name']: field for field in jira.fields()}

            # Populate minions
            all_minions_field = fields.get(JIRA_ALL_MINIONS_FIELD)
            if all_minions_field is None:
                all_minions_field = jira.create_custom_field(
                    name=JIRA_ALL_MINIONS_FIELD,
                    description='The ID of the Salt minions.',
                    type=CustomFieldType.MULTI_SELECT,
                    searcherKey=CustomFieldSearcherKey.MULTI_SELECT,
                )
            log.info('Sync: Clearing current field options for %s on Jira.', JIRA_ALL_MINIONS_FIELD)
            jira.clear_custom_field_options(all_minions_field['id'])
            log.info('Sync: Populating field options for %s on Jira.', JIRA_ALL_MINIONS_FIELD)
            jira.set_custom_field_options(all_minions_field['id'], all_minions)

            # Populate Linux minions
            linux_minions_field = fields.get(JIRA_LINUX_MINIONS_FIELD)
            if linux_minions_field is None:
                linux_minions_field = jira.create_custom_field(
                    name=JIRA_LINUX_MINIONS_FIELD,
                    description='The ID of the Salt minions.',
                    type=CustomFieldType.MULTI_SELECT,
                    searcherKey=CustomFieldSearcherKey.MULTI_SELECT,
                )
            log.info('Sync: Clearing current field options for %s on Jira.', JIRA_LINUX_MINIONS_FIELD)
            jira.clear_custom_field_options(linux_minions_field['id'])
            log.info('Sync: Populating field options for %s on Jira.', JIRA_LINUX_MINIONS_FIELD)
            jira.set_custom_field_options(linux_minions_field['id'], linux_minions)

            # Populate Windows minions
            windows_minions_field = fields.get(JIRA_WINDOWS_MINIONS_FIELD)
            if windows_minions_field is None:
                windows_minions_field = jira.create_custom_field(
                    name=JIRA_WINDOWS_MINIONS_FIELD,
                    description='The ID of the Salt minions.',
                    type=CustomFieldType.MULTI_SELECT,
                    searcherKey=CustomFieldSearcherKey.MULTI_SELECT,
                )
            log.info('Sync: Clearing current field options for %s on Jira.', JIRA_WINDOWS_MINIONS_FIELD)
            jira.clear_custom_field_options(windows_minions_field['id'])
            log.info('Sync: Populating field options for %s on Jira.', JIRA_WINDOWS_MINIONS_FIELD)
            jira.set_custom_field_options(windows_minions_field['id'], windows_minions)

            # Populate Linux packages
            linux_packages_field = fields.get(JIRA_LINUX_PACKAGE_FIELD)
            if linux_packages_field is None:
                linux_packages_field = jira.create_custom_field(
                    name=JIRA_LINUX_PACKAGE_FIELD,
                    description='The system package and version to install, upgrade or downgrade to, or remove.',
                    type=CustomFieldType.CASCADING_SELECT,
                    searcherKey=CustomFieldSearcherKey.CASCADING_SELECT,
                )
            log.info('Sync: Clearing current field options for %s on Jira.', JIRA_LINUX_PACKAGE_FIELD)
            jira.clear_custom_field_options(linux_packages_field['id'])
            log.info('Sync: Populating field options for %s on Jira.', JIRA_LINUX_PACKAGE_FIELD)
            jira.set_custom_field_options(linux_packages_field['id'], linux_packages)

            # Populate Windows packages
            windows_packages_field = fields.get(JIRA_WINDOWS_PACKAGE_FIELD)
            if windows_packages_field is None:
                windows_packages_field = jira.create_custom_field(
                    name=JIRA_WINDOWS_PACKAGE_FIELD,
                    description='The system package and version to install, upgrade or downgrade to, or remove.',
                    type=CustomFieldType.CASCADING_SELECT,
                    searcherKey=CustomFieldSearcherKey.CASCADING_SELECT,
                )
            log.info('Sync: Clearing current field options for %s on Jira.', JIRA_WINDOWS_PACKAGE_FIELD)
            jira.clear_custom_field_options(windows_packages_field['id'])
            log.info('Sync: Populating field options for %s on Jira.', JIRA_WINDOWS_PACKAGE_FIELD)
            jira.set_custom_field_options(windows_packages_field['id'], windows_packages)
        except:
            log.error('Sync: Failed to send data to Jira.', exc_info=True)
            return False

        log.info('Sync: Finished.')
        return True


def jsonify_clear(response):
    if 'successes' in response and not response['successes']:
        del response['successes']
    if 'failures' in response and not response['failures']:
        del response['failures']
    return response


def split_version(version):
    parts = RE_SPLIT_VERSION.split(version)
    for index, part in enumerate(parts):
        try:
            parts[index] = '{0:010d}'.format(int(part))
        except:
            pass
    return parts


def isoparse(timestamp):
    try:
        return dateutil.parser.isoparse(timestamp)
    except:
        pass
    return None


if __name__ == '__main__':
    app.run(debug=os.environ.get('FLASK_ENV') == 'development')
