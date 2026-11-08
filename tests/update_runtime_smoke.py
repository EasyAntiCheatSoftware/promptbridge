#!/usr/bin/env python3
"""Real source update -> native verification -> exec restart -> rollback, offline."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from updater import REMOTE_URL
from runtime_setup import verify_runtime


def call(args,cwd,**kw):
    return subprocess.run(args,cwd=cwd,check=True,capture_output=True,text=True,timeout=120,**kw).stdout.strip()


def main():
    native=ROOT/'runtime/claude';verify_runtime(native)
    with tempfile.TemporaryDirectory(prefix='promptbridge-real-update-') as temporary:
        root=Path(temporary).resolve()/'source';root.mkdir()
        names=call(['git','ls-files','--cached','--others','--exclude-standard'],ROOT).splitlines()
        for name in names:
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/name,path)
        def git(*args): return call(['git','-c','core.hooksPath=/dev/null',*args],root)
        git('init','-b','main');git('config','user.name','Local fixture');git('config','user.email','fixture@example.invalid')
        git('remote','add','origin',REMOTE_URL)
        git('add','-A');git('commit','-m','baseline')
        first=git('rev-parse','HEAD')
        manifest=json.loads((root/'promptbridge.json').read_text())
        before=manifest['version'];major,minor,patch=map(int,before.split('.'));manifest['version']=f'{major}.{minor}.{patch+1}'
        (root/'promptbridge.json').write_text(json.dumps(manifest))
        git('add','-A');git('commit','-m','fixture update');target=git('rev-parse','HEAD');git('reset','--hard',first)
        runtime=root/'runtime';runtime.mkdir();(runtime/'claude').symlink_to(native.resolve())
        shutil.copy2(root/'bootstrap.py',runtime/'bootstrap.py')
        state=root/'state';state.mkdir(mode=0o700)
        (state/'key-fixture').write_text('DUMMY NOT A PROVIDER KEY');(state/'chat-fixture.json').write_text('{"saved":true}')
        hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in state.iterdir()}
        project=Path(temporary).resolve()/'project with spaces';project.mkdir()
        environment=dict(os.environ,PROMPTBRIDGE_STATE_DIR=str(state),PROMPTBRIDGE_COMMAND='promptbridge',PROMPTBRIDGE_NO_UPDATE_CHECK='1')
        install=("from pathlib import Path; import updater; root=Path("+repr(str(root))+");u=updater.Updater(root,Path("+repr(str(state))+"));"
                 +"u.apply(updater.Candidate("+repr(first)+","+repr(target)+","+repr(manifest)+"));updater.restart(root,['--app-version'])")
        # PYTHONPATH chooses the fixture application's own modules, not this test.
        environment['PYTHONPATH']=str(root)
        updated=call([sys.executable,'-B','-c',install],project,env=environment)
        assert updated=='Promptbridge '+manifest['version'],updated
        assert git('rev-parse','HEAD')==target
        rollback=("from pathlib import Path;import updater;root=Path("+repr(str(root))+");u=updater.Updater(root,Path("+repr(str(state))+"));u.rollback_last();updater.restart(root,['--app-version'])")
        restored=call([sys.executable,'-B','-c',rollback],project,env=environment)
        assert restored=='Promptbridge '+before,restored
        assert git('rev-parse','HEAD')==first
        assert hashes=={name:hashlib.sha256((state/name).read_bytes()).hexdigest() for name in hashes}
        print(json.dumps(dict(real_source_update=updated,native_publisher_and_version_verified=True,
                              exec_restart=True,rollback=restored,private_state_unchanged=True,provider_requests=0)))

if __name__=='__main__':main()
