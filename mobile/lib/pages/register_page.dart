import 'dart:async';

import 'package:flutter/material.dart';

import '../api.dart';
import '../store.dart';
import 'home_page.dart';

class RegisterPage extends StatefulWidget {
  const RegisterPage({super.key});

  @override
  State<RegisterPage> createState() => _RegisterPageState();
}

class _RegisterPageState extends State<RegisterPage> {
  late final TextEditingController _server =
      TextEditingController(text: AppStore.baseUrl);
  final TextEditingController _email = TextEditingController();
  final TextEditingController _phone = TextEditingController();
  final TextEditingController _password = TextEditingController();
  final TextEditingController _code = TextEditingController();
  bool _busy = false;
  int _cooldown = 0;
  Timer? _timer;
  String? _error;
  String? _info;

  @override
  void dispose() {
    _timer?.cancel();
    _server.dispose();
    _email.dispose();
    _phone.dispose();
    _password.dispose();
    _code.dispose();
    super.dispose();
  }

  ApiClient _client() => ApiClient(baseUrl: normalizeBaseUrl(_server.text));

  Future<void> _sendCode() async {
    final email = _email.text.trim();
    if (email.isEmpty) {
      setState(() => _error = '请先填写邮箱');
      return;
    }
    setState(() {
      _error = null;
      _info = null;
    });
    try {
      await _client().sendEmailCode(email, 'register');
      setState(() {
        _info = '验证码已发送，请查收邮箱';
        _cooldown = 60;
      });
      _timer?.cancel();
      _timer = Timer.periodic(const Duration(seconds: 1), (timer) {
        if (_cooldown <= 1) {
          timer.cancel();
          setState(() => _cooldown = 0);
        } else {
          setState(() => _cooldown -= 1);
        }
      });
    } on ApiException catch (e) {
      setState(() => _error = e.message);
    }
  }

  Future<void> _register() async {
    final server = normalizeBaseUrl(_server.text);
    final email = _email.text.trim();
    final phone = _phone.text.trim();
    final password = _password.text;
    final code = _code.text.trim();
    if (server.isEmpty || email.isEmpty || password.isEmpty || code.isEmpty) {
      setState(() => _error = '请填写服务器地址、邮箱、密码与验证码');
      return;
    }
    setState(() {
      _busy = true;
      _error = null;
    });
    try {
      final data = await _client().register(
        email: email,
        phone: phone.isEmpty ? null : phone,
        password: password,
        code: code,
      );
      final token = data['token']?.toString() ?? '';
      await AppStore.setBaseUrl(server);
      await AppStore.setToken(token);
      await AppStore.setEmail(email);
      if (!mounted) return;
      Navigator.of(context).pushAndRemoveUntil(
        MaterialPageRoute(builder: (_) => const HomePage()),
        (route) => false,
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
      appBar: AppBar(title: const Text('注册账号')),
      body: ListView(
        padding: const EdgeInsets.all(24),
        children: [
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
          const Text('邮箱', style: TextStyle(fontWeight: FontWeight.w600)),
          const SizedBox(height: 6),
          TextField(
            controller: _email,
            keyboardType: TextInputType.emailAddress,
            decoration: const InputDecoration(border: OutlineInputBorder()),
          ),
          const SizedBox(height: 18),
          const Text('手机号（可选）', style: TextStyle(fontWeight: FontWeight.w600)),
          const SizedBox(height: 6),
          TextField(
            controller: _phone,
            keyboardType: TextInputType.phone,
            decoration: const InputDecoration(
              hintText: '仅作账号标识，不发送短信',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 18),
          const Text('密码', style: TextStyle(fontWeight: FontWeight.w600)),
          const SizedBox(height: 6),
          TextField(
            controller: _password,
            obscureText: true,
            decoration: const InputDecoration(
              hintText: '至少 8 位',
              border: OutlineInputBorder(),
            ),
          ),
          const SizedBox(height: 18),
          const Text('邮箱验证码', style: TextStyle(fontWeight: FontWeight.w600)),
          const SizedBox(height: 6),
          Row(
            children: [
              Expanded(
                child: TextField(
                  controller: _code,
                  keyboardType: TextInputType.number,
                  decoration: const InputDecoration(border: OutlineInputBorder()),
                ),
              ),
              const SizedBox(width: 10),
              OutlinedButton(
                onPressed: (_busy || _cooldown > 0) ? null : _sendCode,
                child: Text(_cooldown > 0 ? '${_cooldown}s' : '发送验证码'),
              ),
            ],
          ),
          if (_error != null) ...[
            const SizedBox(height: 14),
            Text(_error!, style: const TextStyle(color: Colors.red)),
          ],
          if (_info != null) ...[
            const SizedBox(height: 14),
            Text(_info!, style: const TextStyle(color: Colors.green)),
          ],
          const SizedBox(height: 24),
          FilledButton(
            onPressed: _busy ? null : _register,
            child: Padding(
              padding: const EdgeInsets.symmetric(vertical: 12),
              child: _busy
                  ? const SizedBox(
                      height: 20,
                      width: 20,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    )
                  : const Text('注册并登录'),
            ),
          ),
        ],
      ),
    );
  }
}
