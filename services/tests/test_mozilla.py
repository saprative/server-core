# ***** BEGIN LICENSE BLOCK *****
# Version: MPL 1.1/GPL 2.0/LGPL 2.1
#
# The contents of this file are subject to the Mozilla Public License Version
# 1.1 (the "License"); you may not use this file except in compliance with
# the License. You may obtain a copy of the License at
# http://www.mozilla.org/MPL/
#
# Software distributed under the License is distributed on an "AS IS" basis,
# WITHOUT WARRANTY OF ANY KIND, either express or implied. See the License
# for the specific language governing rights and limitations under the
# License.
#
# The Original Code is Sync Server
#
# The Initial Developer of the Original Code is the Mozilla Foundation.
# Portions created by the Initial Developer are Copyright (C) 2010
# the Initial Developer. All Rights Reserved.
#
# Contributor(s):
#   Tarek Ziade (tarek@mozilla.com)
#
# Alternatively, the contents of this file may be used under the terms of
# either the GNU General Public License Version 2 or later (the "GPL"), or
# the GNU Lesser General Public License Version 2.1 or later (the "LGPL"),
# in which case the provisions of the GPL or the LGPL are applicable instead
# of those above. If you wish to allow use of your version of this file only
# under the terms of either the GPL or the LGPL, and not to allow others to
# use your version of this file under the terms of the MPL, indicate your
# decision by deleting the provisions above and replace them with the notice
# and other provisions required by the GPL or the LGPL. If you do not delete
# the provisions above, a recipient may use your version of this file under
# the terms of any one of the MPL, the GPL or the LGPL.
#
# ***** END LICENSE BLOCK *****
import unittest
import wsgi_intercept
from webob import Response
from wsgi_intercept.urllib2_intercept import install_opener
install_opener()

try:
    from services.auth.mozilla import MozillaAuth
    # using the patching from test_ldapsqlauth
    from services.tests.test_ldapsqlauth import patch, unpatch
    LDAP = True
except ImportError:
    LDAP = False


# returns a body that has all the responses we need
def fake_response():
    return Response('{"success": 1, "node": "foo"}')


# returns a body that has all the responses we need
def bad_reset_code_resp():
    return Response("")


class TestLDAPSQLAuth(unittest.TestCase):

    def setUp(self):
        patch()

    def tearDown(self):
        unpatch()

    def test_mozilla_auth(self):
        if not LDAP:
            return

        wsgi_intercept.add_wsgi_intercept('localhost', 80, fake_response)
        auth = MozillaAuth('ldap://localhost',
                        'localhost', 'this_path', 'http')

        auth.create_user('tarek', 'tarek', 'tarek@ziade.org')
        uid = auth.get_user_id('tarek')
        auth_uid = auth.authenticate_user('tarek', 'tarek')
        self.assertEquals(auth_uid, uid)

        #password change with no old password (sreg)
        self.assertTrue(auth.generate_reset_code(uid))
        self.assertTrue(auth.update_password(uid, 'newpass', key='foo'))

        #password change with old password (ldap)
        self.assertTrue(auth.update_password(uid, 'newpass', 'tarek'))
        auth_uid = auth.authenticate_user('tarek', 'newpass')
        self.assertEquals(auth_uid, uid)

        self.assertEquals(auth.get_user_node(uid), 'foo')

        auth.clear_reset_code(uid)
        wsgi_intercept.add_wsgi_intercept('localhost', 80, bad_reset_code_resp)
        self.assertFalse(auth.update_password(uid, 'newpass', key='foo'))


def test_suite():
    suite = unittest.TestSuite()
    suite.addTest(unittest.makeSuite(TestLDAPSQLAuth))
    return suite


if __name__ == "__main__":
    unittest.main(defaultTest="test_suite")
