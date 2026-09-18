import 'package:shared_preferences/shared_preferences.dart';

/// 本地持久化：服务器地址与登录令牌。
class AppStore {
  static const _kBaseUrl = 'base_url';
  static const _kToken = 'token';
  static const _kEmail = 'email';
  static late SharedPreferences _prefs;

  static Future<void> init() async {
    _prefs = await SharedPreferences.getInstance();
  }

  static String get baseUrl => _prefs.getString(_kBaseUrl) ?? '';

  static Future<void> setBaseUrl(String value) => _prefs.setString(_kBaseUrl, value);

  static String get token => _prefs.getString(_kToken) ?? '';

  static Future<void> setToken(String value) => _prefs.setString(_kToken, value);

  static String get email => _prefs.getString(_kEmail) ?? '';

  static Future<void> setEmail(String value) => _prefs.setString(_kEmail, value);

  static Future<void> clearSession() async {
    await _prefs.remove(_kToken);
    await _prefs.remove(_kEmail);
  }
}
