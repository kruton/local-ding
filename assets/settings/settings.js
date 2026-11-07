'use strict';
const el = (id) => document.getElementById(id);
const result = el('result');
const fields = [
  'broker_host',
  'broker_port',
  'broker_user',
  'broker_password',
  'topic_prefix',
  'client_id',
  'poll_ms',
];
async function request(action, values = {}) {
  if (!el('admin_user').value || !el('admin_password').value)
    throw new Error('Enter your camera admin username and password.');
  const response = await fetch(`api/${action}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'X-Local-Ding-Settings': '1' },
    cache: 'no-store',
    credentials: 'same-origin',
    body: JSON.stringify({
      ...values,
      admin_user: el('admin_user').value,
      admin_password: el('admin_password').value,
    }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Request failed.');
  return data;
}
async function load() {
  const data = await request('status');
  for (const name of fields)
    if (name !== 'broker_password' && data[name] !== undefined)
      el(name).value = data[name];
  el('broker_password').value = '';
  el('mqtt-status').textContent = data.configured
    ? `Configuration saved. Broker password ${data.broker_password_set ? 'is set' : 'is empty'}.`
    : 'MQTT is not configured yet.';
  if (data.doorbell_source === 'router')
    el('mqtt-status').textContent += data.doorbell_subscriber_ready
      ? ' Doorbell event connection is ready.'
      : ' Doorbell event connection is waiting.';
  if (data.time) {
    el('time_mode').value = data.time.mode;
    el('time_servers').disabled = data.time.mode === 'auto';
    el('time_servers').value = data.time.servers;
    el('time_status').textContent = data.time.running
      ? `NTP servers: ${data.time.effective_servers}. ${data.time.last_sync ? 'Last reported stratum: ' + data.time.last_sync.stratum : 'Waiting for synchronization.'}`
      : 'Time synchronization pending; settings remain available.';
  }
  el('keys').textContent = (data.authorized_keys || []).join('\n') || 'No keys found.';
}
async function run(action) {
  const buttons = [...document.querySelectorAll('button')];
  buttons.forEach((b) => (b.disabled = true));
  result.textContent = 'Working…';
  try {
    result.textContent = await action();
  } catch (error) {
    result.textContent = error.message;
  } finally {
    buttons.forEach((b) => (b.disabled = false));
  }
}
el('load').addEventListener('click', () =>
  run(async () => {
    await load();
    return 'Settings loaded.';
  }),
);
el('mqtt').addEventListener('submit', (event) => {
  event.preventDefault();
  run(async () => {
    const values = Object.fromEntries(fields.map((name) => [name, el(name).value]));
    const data = await request('mqtt', values);
    el('broker_password').value = '';
    await load();
    return data.message;
  });
});
el('ssh').addEventListener('submit', (event) => {
  event.preventDefault();
  run(async () => {
    const data = await request('key', { public_key: el('public_key').value.trim() });
    el('public_key').value = '';
    await load();
    return data.message;
  });
});

el('time').addEventListener('submit', (event) => {
  event.preventDefault();
  run(async () => {
    const data = await request('time', {
      mode: el('time_mode').value,
      servers:
        el('time_mode').value === 'manual' ? el('time_servers').value.trim() : '',
    });
    await load();
    return data.message;
  });
});

el('time_mode').addEventListener('change', () => {
  el('time_servers').disabled = el('time_mode').value === 'auto';
});
