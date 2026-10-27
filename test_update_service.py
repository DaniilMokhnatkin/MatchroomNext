import io
from urllib.error import HTTPError
import json
import unittest
from unittest.mock import patch
import update_service as u

class UpdateTests(unittest.TestCase):
    def response(self, release):
        class Response(io.BytesIO):
            pass
        return Response(json.dumps(release).encode())
    def release(self, tag='v0.10'):
        return {'tag_name': tag, 'assets': [{'name': f'Matchroom.Next.{tag}.exe', 'size': 2048,
            'digest': 'sha256:' + 'a'*64,
            'browser_download_url': f'https://github.com/{u.REPOSITORY}/releases/download/{tag}/Matchroom.Next.{tag}.exe'}]}
    def test_newer_and_equal(self):
        with patch.object(u, 'urlopen', return_value=self.response(self.release())):
            self.assertTrue(u.check('next-0.9')['available'])
        with patch.object(u, 'urlopen', return_value=self.response(self.release())):
            self.assertFalse(u.check('next-0.10')['available'])
    def test_untrusted_or_missing_checksum_rejected(self):
        for change in ({'digest': None}, {'browser_download_url': 'https://example.com/update.exe'}):
            release = self.release(); release['assets'][0].update(change)
            with patch.object(u, 'urlopen', return_value=self.response(release)):
                with self.assertRaises((ValueError, TypeError)): u.check('next-0.9')
    def test_rate_limit_fallback(self):
        class Page(io.BytesIO):
            def geturl(self):
                return 'https://github.com/' + u.REPOSITORY + '/releases/tag/v0.10'
        denied = HTTPError(u.API, 403, 'limited', {}, None)
        with patch.object(u, 'urlopen', side_effect=[denied, Page(b''), io.BytesIO(b'a'*64)]):
            result = u.check('next-0.9')
        self.assertTrue(result['available'])
        self.assertEqual(result['digest'], 'a'*64)
        self.assertIsNone(result['size'])
        self.assertTrue(result['url'].endswith('/v0.10/Matchroom.Next.v0.10.exe'))
    def test_bad_version(self):
        with self.assertRaises(ValueError): u.version('v0.9-evil')

if __name__ == '__main__': unittest.main()
