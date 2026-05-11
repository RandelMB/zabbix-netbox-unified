<?php

$config['db_host'] = getenv('OBSERVIUM_DB_HOST');
$config['db_name'] = getenv('OBSERVIUM_DB_NAME');
$config['db_user'] = getenv('OBSERVIUM_DB_USER');
$config['db_pass'] = getenv('OBSERVIUM_DB_PASS');
$config['base_url'] = getenv('OBSERVIUM_BASE_URL');

// Local support instance: UI and metadata only, without continuous polling.
$config['web_show_disabled'] = TRUE;
$config['poller-wrapper']['alerter'] = FALSE;
$config['snmp']['timeout'] = 1;
$config['snmp']['retries'] = 0;
$config['snmp']['max-rep'] = TRUE;
$config['login_message'] = 'Local Observium support instance: metadata-only mode, no automatic poller.';

