import 'package:flutter/material.dart';

import '../api.dart';
import '../store.dart';
import 'home_page.dart';
import 'register_page.dart';

class LoginPage extends StatefulWidget {
  const LoginPage({super.key});

  @override
  State<LoginPage> createState() => _LoginPageState();
}

class _LoginPageState extends State<LoginPage> {
  late final TextEditingController _server =
      TextEditingController(text: AppStore.baseUrl);
  final TextEditingController _account = TextEditingController();
  final TextEditingController _password = TextEditingController();
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _server.dispose();
    _account.dispose();
    _password.dispose();
    super.dispose();
  }

  Future<void> _login() async {
    final server = normalizeBaseUrl(_server.text);
    final account = _account.text.trim();
    final password = _password.text;
    if (server.isEmpty || account.isEmpty || password.isEmpty) {
      setState(() => _error = '请填写服务器地址、账号与密码');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    final api = ApiClient(baseUrl: server);
    try {
      final data = await api.login(account, password);
      final token = data['token']?.toString() ?? '';
      final email = (data['account'] as Map?)?['email']?.toString() ?? account;
      await AppStore.setBaseUrl(server);
      await AppStore.setToken(token);
      await AppStore.setEmail(email);
      if (!mounted) return;
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => const HomePage()),
      );
    } on ApiException catch (e) {
      setState(() => _error = e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Maagent 登录')),
      body: ListView(
        padding: const EdgeInsets.all(24),
        children: [
          const SizedBox(height: 8),
          const Text('服务器地址', style: TextStyle(fontWeight: FontWeight.w600)),
          const SizedBox(height: 6),
          TextField(
            controller: _server,
            keyboardType: TextInputType.url,
            decoration: const InputDecoration(
              hintText: 'http://100.x.y.z:8765',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 18),
          const Text('邮箱或手机号', style: TextStyle(fontWeight: FontWeight.w600)),
          const SizedBox(height: 6),
          TextField(
            controller: _account,
            keyboardType: TextInputType.emailAddress,
            decoration: const InputDecoration(border: OutlineInputBorder()),
          ),
          const SizedBox(height: 18),
          const Text('密码', style: TextStyle(fontWeight: FontWeight.w600)),
          const SizedBox(height: 6),
          TextField(
            controller: _password,
            obscureText: true,
            decoration: const InputDecoration(border: OutlineInputBorder()),
            onSubmitted: (_) => _busy ? null : _login(),
          ),
          if (_error != null) ...[
            const SizedBox(height: 14),
            Text(_error!, style: const TextStyle(color: Colors.red)),
          ],
          const SizedBox(height: 24),
          FilledButton(
            onPressed: _busy ? null : _login,
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 12),
              child: _busy
                  ? const SizedBox(
                      height: 20,
                      width: 20,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Text('登录'),
            ),
          ),
          TextButton(
            onPressed: _busy
                ? null
                : () => Navigator.of(context).push(
                      MaterialPageRoute(builder: (_) => const RegisterPage()),
                    ),
            child: const Text('还没有账号？注册'),
          ),
        ],
      ),
    );
  }
}
