"""Focused checks for path mapping and downloaded-byte verification."""
import base64
import hashlib
import unittest
from unittest.mock import Mock
from stage_server_changes import validate_path,remote_path,decode_result,is_link

class StagingTests(unittest.TestCase):
    def test_rejects_paths_and_private_assets(self):
        for path in ['../x.py','/x.py','web/../x.py','web/.env.json','web/release/a.json','web/node_modules/a.js','engineering/data/platform/s.json','web/a\\b.ts','web/C:evil.ts','web/CON.py','web/a.db']:
            with self.subTest(path=path), self.assertRaises(ValueError):validate_path(path)

    def test_explicit_worktree_mapping(self):
        self.assertEqual(remote_path('web/src/App.tsx','main'),'02_比赛作品/web/src/App.tsx')
        self.assertEqual(remote_path('web/src/App.tsx','ui-enterprise-01'),'02_比赛作品-ui-enterprise-01/web/src/App.tsx')
        self.assertEqual(remote_path('engineering/src/capabilities.py','main'),'01_工程项目/src/capabilities.py')
        with self.assertRaises(ValueError):remote_path('web/src/App.tsx','other')

    def test_transport_bytes_and_hash(self):
        data=b'synthetic source\n';item={'data':base64.b64encode(data).decode(),'size':len(data),'sha256':hashlib.sha256(data).hexdigest()}
        self.assertEqual(decode_result(item),data)
        for field,value in [('size',0),('sha256','0'*64),('data','!invalid')]:
            with self.subTest(field=field),self.assertRaises((ValueError,base64.binascii.Error)):
                decode_result({**item,field:value})

    def test_rejects_private_key(self):
        data=b'-----BEGIN OPENSSH PRIVATE KEY-----'
        with self.assertRaises(ValueError):decode_result({'data':base64.b64encode(data).decode(),'size':len(data),'sha256':hashlib.sha256(data).hexdigest()})

    def test_windows_junction_is_treated_as_a_link(self):
        path=Mock();path.is_symlink.return_value=False;path.is_junction.return_value=True
        self.assertTrue(is_link(path))

if __name__=='__main__':unittest.main()
