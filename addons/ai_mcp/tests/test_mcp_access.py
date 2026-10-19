import json

from odoo.tests import HttpCase, tagged


@tagged("post_install", "-at_install")
class TestMcpAccess(HttpCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.env["ir.config_parameter"].sudo().set_str("ai_assistant.api_key", "shared-mcp-key-0123456789abcdef")
        cls.keys = {}
        for login, groups in (("admin", ["base.group_user", "dojo_core.group_dojo_admin"]),
                              ("coach", ["base.group_user", "dojo_core.group_dojo_instructor"]),
                              ("clerk", ["base.group_user"]),
                              ("parent", ["base.group_portal"])):
            user = cls.env["res.users"].with_context(no_reset_password=True).create({
                "name": "MCP " + login, "login": "mcp-" + login + "@example.invalid",
                "group_ids": [(6, 0, [cls.env.ref(xmlid).id for xmlid in groups])]})
            cls.keys[login] = cls.env["res.users.apikeys"].with_user(user).sudo()._generate("rpc", "MCP test", None)

    def rpc(self, method, params=None, key=None, role=None, bearer=True):
        headers = {"Content-Type": "application/json"}
        if key:
            headers["Authorization" if bearer else "X-Api-Key"] = ("Bearer " + key) if bearer else key
        if role:
            headers["X-Mcp-Role"] = role
        response = self.url_open("/mcp", data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}),
                                 headers=headers)
        self.assertEqual(response.status_code, 200)
        return response.json()

    def tools(self, **kw):
        return {tool["name"] for tool in self.rpc("tools/list", **kw)["result"]["tools"]}

    def test_handshake_needs_no_key_but_everything_else_does(self):
        self.assertEqual(self.rpc("initialize")["result"]["serverInfo"]["name"], "ai-mcp-server")
        self.assertEqual(self.rpc("tools/list")["error"]["code"], -32001)

    def test_the_old_shared_key_no_longer_works(self):
        for bearer in (True, False):
            body = self.rpc("tools/list", key="shared-mcp-key-0123456789abcdef", role="admin", bearer=bearer)
            self.assertEqual(body["error"]["message"], "Invalid API key")

    def test_role_comes_from_the_odoo_user_not_the_header(self):
        coach = self.tools(key=self.keys["coach"], role="admin")
        self.assertIn("belt_promote", coach)
        self.assertNotIn("bill_create", coach)
        self.assertNotIn("employee_create", coach)
        admin = self.tools(key=self.keys["admin"])
        self.assertTrue({"bill_create", "employee_create", "belt_promote"} <= admin)
        self.assertGreater(len(admin), len(coach))

    def test_admin_only_tools_are_refused_to_instructors(self):
        body = self.rpc("tools/call", {"name": "bill_create", "arguments": {}}, key=self.keys["coach"], role="admin")
        self.assertTrue(body["result"]["isError"])
        self.assertIn("Permission denied: role 'instructor'", body["result"]["content"][0]["text"])

    def test_the_header_can_narrow_a_role(self):
        kiosk = self.tools(key=self.keys["admin"], role="kiosk")
        self.assertIn("attendance_checkin", kiosk)
        self.assertNotIn("belt_promote", kiosk)

    def test_accounts_without_a_staff_role_are_refused(self):
        self.assertEqual(self.rpc("tools/list", key=self.keys["clerk"])["error"]["message"], "This account has no Dojang staff role")
        self.assertEqual(self.rpc("tools/list", key=self.keys["parent"])["error"]["message"], "This account can't use the MCP server")
        self.assertEqual(self.rpc("tools/list", key="not-a-real-key-0123456789")["error"]["message"], "Invalid API key")
