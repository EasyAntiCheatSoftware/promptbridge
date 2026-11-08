"""Small, dependency-free terminal dashboard for macOS and Linux."""
from __future__ import annotations
import contextlib
import getpass
import json
import os
from pathlib import Path
import select
import shlex
import shutil
import subprocess
import sys
import tempfile
import termios
import tty
import uuid
import bridge
from opencode_models import atomic_json, cached_catalog, fetch_catalog, is_free, model_name, saved_model
from updater import UpdateError, metadata, read_object, restart


def safe(value):
    return ''.join(character for character in str(value) if character.isprintable())


class Console:
    def __init__(self, color='auto'):
        self.color = color

    def paint(self, text, code='36'):
        enabled = (self.color != 'never' and 'NO_COLOR' not in os.environ
                   and os.environ.get('TERM') != 'dumb'
                   and (self.color == 'always' or sys.stdout.isatty()))
        value = safe(text)
        return f'\x1b[{code}m{value}\x1b[0m' if enabled else value

    def header(self, title, detail=''):
        width = min(86, max(36, shutil.get_terminal_size((80, 24)).columns - 2))
        print('\n' + self.paint('─' * width, '34'))
        print('  ' + self.paint(title, '1;36'))
        if detail: print('  ' + self.paint(detail[:width - 4], '2'))
        print(self.paint('─' * width, '34'))

    def message(self, text, *, error=False):
        print(self.paint(text, '31' if error else '32'), flush=True)

    def pause(self):
        try: input('\nPress Enter to return… ')
        except (EOFError, KeyboardInterrupt): pass


def read_key():
    fd = sys.stdin.fileno()
    previous = termios.tcgetattr(fd)
    try:
        tty.setraw(fd, when=termios.TCSANOW)
        first = os.read(fd, 1)
        if not first: return 'back'
        if first == b'\x1b':
            tail = b''
            for _ in range(3):
                if not select.select([fd], [], [], .08)[0]: break
                tail += os.read(fd, 1)
                if tail.endswith((b'A', b'B', b'C', b'D')): break
            return {b'[A': 'up', b'OA': 'up', b'[B': 'down', b'OB': 'down'}.get(tail, 'back')
        return {b'\r': 'enter', b'\n': 'enter', b'\x03': 'back', b'\x04': 'back'}.get(first, first.decode(errors='ignore'))
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)


def menu(console, options, *, default=0):
    """Options are (label, value); numbers also work without raw-terminal support."""
    if not options: return None
    size = shutil.get_terminal_size((80, 24))
    raw = (sys.stdin.isatty() and sys.stdout.isatty() and os.environ.get('TERM') != 'dumb'
           and size.columns >= 40 and size.lines >= len(options) + 8)
    if not raw:
        for index, (label, _) in enumerate(options, 1): print(f'  {index}. {safe(label)}')
        while True:
            try: answer = input('Choice (Enter selects highlighted/default; q goes back): ').strip()
            except (EOFError, KeyboardInterrupt): return None
            if not answer: return options[min(max(default, 0), len(options) - 1)][1]
            if answer.casefold() == 'q': return None
            if answer.isascii() and answer.isdigit() and len(answer) < 4 and 1 <= int(answer) <= len(options):
                return options[int(answer) - 1][1]
            console.message('Choose a listed number, Enter for the default, or q to go back.', error=True)
    current = min(max(default, 0), len(options) - 1)
    lines = len(options) + 1
    def draw():
        for index, (label, _) in enumerate(options):
            prefix = ' >' if index == current else '  '
            width = max(20, shutil.get_terminal_size((80, 24)).columns - 2)
            line = f'{prefix} {index + 1}. {safe(label)}'
            if len(line) > width: line = line[:width - 1] + '…'
            print('\r\x1b[2K' + console.paint(line, '1;36' if index == current else '37'))
        print('\r\x1b[2K' + console.paint('  ↑/↓ or j/k · Enter to choose · number shortcuts · q to go back', '2'))
        sys.stdout.flush()
    draw()
    try:
        while True:
            key = read_key()
            if key in ('back', 'q', 'Q'): return None
            if key == 'enter': return options[current][1]
            if key.isascii() and key.isdigit() and key != '0' and int(key) <= len(options):
                return options[int(key) - 1][1]
            if key in ('up', 'k'): current = (current - 1) % len(options)
            elif key in ('down', 'j'): current = (current + 1) % len(options)
            else: continue
            print(f'\x1b[{lines}A', end='')
            draw()
    except (EOFError, KeyboardInterrupt): return None


def ui_preferences(state):
    try: value = read_object(state / 'ui.json')
    except (UpdateError, OSError): value = {}
    return {'free_only': value.get('free_only', True) is not False,
            'prompt_mode': value.get('prompt_mode') if value.get('prompt_mode') in ('append', 'replace', 'off') else 'off',
            'color': value.get('color') if value.get('color') in ('auto', 'never', 'always') else 'auto'}


def save_ui_preferences(state, **changes):
    try: read_object(state / 'ui.json')
    except UpdateError:
        path = state / 'ui.json'
        if path.is_symlink() or path.stat().st_size > 32768:
            raise UpdateError('Linked or oversized interface settings preserved; repair the path first.')
        backup = state / ('ui.invalid-' + uuid.uuid4().hex + '.json')
        managed = path.read_bytes()
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as file: file.write(managed)
        print('Invalid interface settings were backed up before saving your choice.')
    value = ui_preferences(state); value.update(changes)
    atomic_json(state, 'ui.json', value)


def chat_arguments(state, action='new'):
    preferences = ui_preferences(state)
    model = saved_model(state) or bridge.selected_model()
    args = ['--model', model]
    if preferences['free_only']: args.append('--free-only')
    prompt = state / 'system-prompt.md'
    if preferences['prompt_mode'] != 'off':
        if not prompt.is_file(): raise ValueError('Your saved instructions are missing. Open System instructions to repair them.')
        args += ['--append-system-prompt-file' if preferences['prompt_mode'] == 'append' else '--system-prompt-file', str(prompt)]
    if action == 'continue': args.append('--continue')
    if action == 'resume': args.append('--resume')
    return args


def edit_prompt(state, console):
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = state / 'system-prompt.md'
    if target.is_symlink(): raise ValueError('Instruction file is linked; existing file preserved.')
    existing = target.read_text() if target.exists() else ''
    editor = os.environ.get('VISUAL') or os.environ.get('EDITOR') or shutil.which('nano') or shutil.which('vi')
    if not editor:
        print('Write your instructions. Finish with a line containing only .')
        lines = []
        while True:
            try: line = input()
            except (EOFError, KeyboardInterrupt): return
            if line == '.': break
            lines.append(line)
        content = '\n'.join(lines) + '\n'
    else:
        fd, name = tempfile.mkstemp(prefix='.prompt-', suffix='.md', dir=state)
        stage = Path(name)
        try:
            with os.fdopen(fd, 'w') as file: file.write(existing)
            if subprocess.call([*shlex.split(editor), str(stage)]):
                console.message('Editor cancelled; previous instructions retained.'); return
            if stage.stat().st_size > 128 * 1024: raise ValueError('Instructions exceed the 128 KiB limit.')
            content = stage.read_text()
        finally: stage.unlink(missing_ok=True)
    if len(content.encode()) > 128 * 1024: raise ValueError('Instructions exceed the 128 KiB limit.')
    fd, name = tempfile.mkstemp(prefix='.prompt-save-', dir=state); stage = Path(name)
    try:
        with os.fdopen(fd, 'w') as file: file.write(content); file.flush(); os.fsync(file.fileno())
        os.replace(stage, target)
    finally: stage.unlink(missing_ok=True)
    if ui_preferences(state)['prompt_mode'] == 'off': save_ui_preferences(state, prompt_mode='append')
    console.message('Instructions saved. They will be reapplied when you start or resume a chat.')


def instructions(state, console):
    while True:
        preference = ui_preferences(state)
        console.header('System instructions', 'Saved locally · mode: ' + preference['prompt_mode'])
        choice = menu(console, [('Edit saved instructions', 'edit'), ('Append to normal coding instructions', 'append'),
                                ('Replace normal coding instructions', 'replace'), ('Disable saved instructions', 'off'), ('Back', 'back')])
        if choice in (None, 'back'): return
        if choice == 'edit': edit_prompt(state, console)
        else:
            if choice != 'off' and not (state / 'system-prompt.md').is_file(): edit_prompt(state, console)
            if choice == 'off' or (state / 'system-prompt.md').is_file():
                save_ui_preferences(state, prompt_mode=choice); console.message('Instruction mode saved: ' + choice)


def choose_model(state, console):
    console.message('Refreshing OpenCode model names and catalog prices…')
    try: catalog = fetch_catalog(state)
    except (OSError, ValueError):
        catalog = cached_catalog(state)
        if not catalog: raise ValueError('Model list is unavailable. Check your connection and try again.')
        console.message('Using the saved model list; account access is not verified.', error=True)
    free_only = ui_preferences(state)['free_only']; query = ''; page = 0
    while True:
        choices = sorted((item for item in catalog if (not free_only or is_free(item))
                          and query.casefold() in (model_name(item) + ' ' + item['id']).casefold()),
                         key=lambda item: (not is_free(item), model_name(item).casefold()))
        pages = max(1, (len(choices) + 5) // 6); page %= pages
        console.header('Choose a model', f'{len(choices)} matches · page {page + 1}/{pages} · ' + ('free-listed only' if free_only else 'all catalog prices'))
        print(console.paint('  FREE is catalog pricing, not permission or a billing guarantee.', '33'))
        rows = [(model_name(item) + (' [FREE] ' if is_free(item) else ' [PAID/UNKNOWN] ') + item['id'], ('model', item)) for item in choices[page * 6:(page + 1) * 6]]
        if pages > 1: rows.append(('Next page', ('page', None)))
        rows += [('Search by name or model ID', ('search', None)), ('Show all models' if free_only else 'Show free-listed models only', ('filter', None))]
        selected = menu(console, rows)
        if selected is None: return
        action, item = selected
        if action == 'page': page += 1
        elif action == 'search':
            try: query = input('Search (empty shows all): ').strip(); page = 0
            except (EOFError, KeyboardInterrupt): return
        elif action == 'filter': free_only = not free_only; page = 0
        elif action == 'model':
            if not is_free(item) and ui_preferences(state)['free_only']:
                print(console.paint('This model has paid or unknown pricing. Explicitly allow paid usage?', '33'))
                if menu(console, [('Keep free-only enabled', False), ('Allow paid/unknown models in the dashboard', True)]) is not True: continue
                save_ui_preferences(state, free_only=False)
            atomic_json(state, 'model.json', {'model': item['id']})
            console.message('Saved default: ' + model_name(item)); return


def set_key(state, console):
    console.header('OpenCode API key', 'Hidden input · saved only for Promptbridge')
    try: value = getpass.getpass('New API key (input hidden; empty cancels): ').strip()
    except (EOFError, KeyboardInterrupt): return
    if not value: return
    if any(character.isspace() for character in value): raise ValueError('API keys must not contain whitespace.')
    bridge.save_key(state, value)
    console.message('Key saved privately. Existing OpenCode and Claude credentials were not changed.')


def offer_update(updater, console, candidate, arguments):
    console.header('Update available', f'Promptbridge {candidate.version} · {candidate.target[:8]}')
    if candidate.subject: print('  ' + safe(candidate.subject)[:100])
    choice = menu(console, [('Update and restart', 'update'), ('Skip for now', 'skip'),
                            ('Skip until the next version', 'ignore')])
    if choice == 'ignore': updater.skip_version(candidate); console.message('This version will no longer prompt.')
    if choice == 'update':
        console.message('Checking files and applying the update…')
        updater.apply(candidate)
        console.message('Update verified. Restarting Promptbridge…')
        restart(updater.root, arguments)
    return choice


def updates(updater, console, arguments):
    while True:
        enabled = updater.preferences_value().get('automatic_checks', True) is not False
        console.header('Updates', 'Original GitHub repository · confirmation required before changing files')
        choice = menu(console, [('Check for updates now', 'check'),
                                ('Turn automatic checks ' + ('off' if enabled else 'on'), 'toggle'),
                                ('Sign in to GitHub in your browser', 'login'), ('Roll back the last update', 'rollback'), ('Back', 'back')])
        if choice in (None, 'back'): return
        if choice == 'toggle': updater.set_preference(automatic_checks=not enabled)
        elif choice == 'login':
            executable = shutil.which('gh')
            if not executable: console.message('GitHub CLI is not installed. Git credentials can also provide repository access.', error=True)
            else: subprocess.call([executable, 'auth', 'login', '--hostname', 'github.com', '--web'])
        elif choice == 'check':
            console.message('Checking GitHub…'); candidate = updater.check(force=True)
            if candidate: offer_update(updater, console, candidate, arguments)
            else: console.message('You are up to date.')
        elif choice == 'rollback':
            if menu(console, [('Keep current version', False), ('Restore the previous version and restart', True)]) is True:
                updater.rollback_last(); restart(updater.root, arguments)


def settings(state, console, updater):
    while True:
        preference = ui_preferences(state)
        console.header('Settings and status')
        try: _, source = bridge.credential(); print('  Key source: ' + safe(source))
        except (OSError, ValueError): print('  API key needs attention.')
        print('  Private files: ' + safe(state)); print('  Model access and quotas are controlled by OpenCode.')
        choice = menu(console, [('Free-only: ' + ('on' if preference['free_only'] else 'off') + ' (toggle)', 'free'),
                                ('Colors: ' + preference['color'] + ' (toggle)', 'color'), ('Back', 'back')])
        if choice in (None, 'back'): return
        if choice == 'free':
            new = not preference['free_only']
            if new or menu(console, [('Keep free-only enabled', False), ('Allow explicitly chosen paid/unknown models', True)]) is True:
                save_ui_preferences(state, free_only=new)
        elif choice == 'color':
            mode = 'never' if preference['color'] != 'never' else 'auto'
            save_ui_preferences(state, color=mode); console.color = mode


def dashboard(root, state, updater, launch_chat):
    console = Console(ui_preferences(state)['color'])
    while True:
        try:
            version = metadata(root)['version']
            try: identifier = saved_model(state) or bridge.selected_model()
            except ValueError: identifier = 'Choose a model to repair saved settings'
            catalog = cached_catalog(state)
            item = next((item for item in catalog if item['id'] == identifier), {'id': identifier})
            preference = ui_preferences(state)
            console.header('PROMPTBRIDGE  ' + version, str(Path.cwd()))
            print('  Model: ' + console.paint(model_name(item), '1;36'))
            print('  Free-only: ' + console.paint('on' if preference['free_only'] else 'off', '32' if preference['free_only'] else '33')
                  + '  |  Instructions: ' + preference['prompt_mode'])
            print(console.paint('  API access depends on provider rules, model permissions and quota.', '2'))
            choice = menu(console, [('Start a new chat', 'new'), ('Continue the last chat in this folder', 'continue'),
                                    ('Choose a saved chat to resume', 'resume'), ('Choose a model', 'model'),
                                    ('System instructions', 'prompt'), ('Set or replace API key', 'key'),
                                    ('Updates', 'updates'), ('Settings and status', 'settings'), ('Quit', 'quit')])
            if choice in (None, 'quit'): return 0
            if choice in ('new', 'continue', 'resume'):
                action = {'new': 'Starting a new chat', 'continue': 'Continuing the last chat',
                          'resume': 'Opening the saved-chat picker'}[choice]
                console.message(action + '… Checking model settings and starting the coding CLI.')
                arguments = chat_arguments(state, choice)
                result = launch_chat(arguments)
                if result not in (None, 0):
                    console.message(f'Chat launch/session ended with exit status {result}. '
                                    'Review the message above. Check the API key, selected model and provider access; '
                                    'your saved chats are retained.', error=True)
                    console.pause()
                else:
                    console.message('Returned from the coding CLI. Saved chats are retained.')
            elif choice == 'model': choose_model(state, console)
            elif choice == 'prompt': instructions(state, console)
            elif choice == 'key': set_key(state, console)
            elif choice == 'updates': updates(updater, console, [])
            elif choice == 'settings': settings(state, console, updater)
        except (OSError, ValueError, UpdateError) as exc:
            console.message(str(exc), error=True); console.pause()
        except (EOFError, KeyboardInterrupt):
            print(); return 0
