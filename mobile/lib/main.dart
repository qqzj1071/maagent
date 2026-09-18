import 'package:flutter/material.dart';

import 'pages/home_page.dart';
import 'pages/login_page.dart';
import 'store.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  await AppStore.init();
  runApp(const MaagentApp());
}

class MaagentApp extends StatelessWidget {
  const MaagentApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Maagent',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorSchemeSeed: const Color(0xFF3B82F6),
        useMaterial3: true,
      ),
      home: AppStore.token.isEmpty ? const LoginPage() : const HomePage(),
    );
  }
}
