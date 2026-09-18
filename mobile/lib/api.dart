import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

class ApiException implements Exception {
  final int status;
  final String error;
  final String message;

  ApiException(this.status, this.error, this.message);

  @override
  String toString() => message;
}

String normalizeBaseUrl(String raw) {
  var value = raw.trim();
  if (value.isEmpty) return value;
  if (!value.startsWith('http://') && !value.startsWith('https://')) {
    value = 'http://$value';
  }
  while (value.endsWith('/')) {
    value = value.substring(0, value.length - 1);
  }
  if (value.endsWith('/api/v1')) {
    value = value.substring(0, value.length - '/api/v1'.length);
  }
  return value;
}

class ApiClient {
  ApiClient({required String baseUrl, this.token}) : baseUrl = normalizeBaseUrl(baseUrl);

  final String baseUrl;
  String? token;

  Uri _uri(String path, [Map<String, String>? query]) {
    final uri = Uri.parse('$baseUrl/api/v1$path');
    if (query == null || query.isEmpty) return uri;
    return uri.replace(queryParameters: query);
  }

  Map<String, String> get _headers {
    final headers = <String, String>{'Content-Type': 'application/json'};
    final value = token;
    if (value != null && value.isNotEmpty) {
      headers['Authorization'] = 'Bearer $value';
    }
    return headers;
  }

  Future<Map<String, dynamic>> _request(
    String method,
    String path, {
    Map<String, dynamic>? body,
    Map<String, String>? query,
  }) async {
    final uri = _uri(path, query);
    http.Response response;
    try {
      if (method == 'GET') {
        response = await http
            .get(uri, headers: _headers)
            .timeout(const Duration(seconds: 15));
      } else {
        response = await http
            .post(
              uri,
              headers: _headers,
              body: body == null ? null : jsonEncode(body),
            )
            .timeout(const Duration(seconds: 30));
      }
    } on TimeoutException {
      throw ApiException(0, 'timeout', '请求超时，请检查服务器地址与网络');
    } catch (e) {
      throw ApiException(0, 'network', '无法连接服务器：$e');
    }

    Map<String, dynamic> decoded = <String, dynamic>{};
    if (response.bodyBytes.isNotEmpty) {
      try {
        final parsed = jsonDecode(utf8.decode(response.bodyBytes));
        if (parsed is Map) decoded = parsed.cast<String, dynamic>();
      } catch (_) {
        decoded = <String, dynamic>{};
      }
    }
    if (response.statusCode >= 400) {
      throw ApiException(
        response.statusCode,
        decoded['error']?.toString() ?? 'error',
        decoded['message']?.toString() ?? '请求失败（${response.statusCode}）',
      );
    }
    return decoded;
  }

  // -- account -------------------------------------------------------- //
  Future<void> sendEmailCode(String email, String purpose) =>
      _request('POST', '/auth/email/code', body: {'email': email, 'purpose': purpose});

  Future<Map<String, dynamic>> register({
    required String email,
    String? phone,
    required String password,
    required String code,
  }) =>
      _request('POST', '/auth/register', body: {
        'email': email,
        'phone': phone,
        'password': password,
        'code': code,
      });

  Future<Map<String, dynamic>> login(String account, String password) =>
      _request('POST', '/auth/login', body: {'account': account, 'password': password});

  Future<void> logout() => _request('POST', '/auth/logout');

  Future<Map<String, dynamic>> me() => _request('GET', '/account/me');

  // -- remote control ------------------------------------------------- //
  Future<Map<String, dynamic>> status() => _request('GET', '/status');

  Future<Map<String, dynamic>> runtimeConfig() => _request('GET', '/config');

  Future<Map<String, dynamic>> startWorkflow([List<String>? software]) => _request(
        'POST',
        '/workflow/start',
        body: software == null ? <String, dynamic>{} : {'software': software},
      );

  Future<Map<String, dynamic>> stopWorkflow() => _request('POST', '/workflow/stop');

  Future<List<dynamic>> logs({int limit = 200}) async {
    final data = await _request('GET', '/logs', query: {'limit': '$limit'});
    return (data['lines'] as List?) ?? const [];
  }

  Future<List<dynamic>> reports({int limit = 20}) async {
    final data = await _request('GET', '/reports', query: {'limit': '$limit'});
    return (data['reports'] as List?) ?? const [];
  }

  Future<Map<String, dynamic>?> latestReport() async {
    final data = await _request('GET', '/reports/latest');
    return (data['report'] as Map?)?.cast<String, dynamic>();
  }

  Future<Map<String, dynamic>?> reportDetail(String id) async {
    final data = await _request('GET', '/reports/detail', query: {'id': id});
    return (data['report'] as Map?)?.cast<String, dynamic>();
  }

  /// 实时事件流：snapshot / status / log / report。
  Stream<Map<String, dynamic>> events() async* {
    final client = http.Client();
    try {
      final request = http.Request('GET', _uri('/events'));
      final value = token;
      if (value != null && value.isNotEmpty) {
        request.headers['Authorization'] = 'Bearer $value';
      }
      request.headers['Accept'] = 'text/event-stream';
      final response = await client.send(request);
      if (response.statusCode >= 400) {
        throw ApiException(response.statusCode, 'sse', '事件流连接失败');
      }
      final lines =
          response.stream.transform(utf8.decoder).transform(const LineSplitter());
      final buffer = <String>[];
      await for (final line in lines) {
        if (line.isEmpty) {
          if (buffer.isNotEmpty) {
            final data = buffer
                .where((l) => l.startsWith('data:'))
                .map((l) => l.substring(5).trim())
                .join();
            if (data.isNotEmpty) {
              try {
                final parsed = jsonDecode(data);
                if (parsed is Map) yield parsed.cast<String, dynamic>();
              } catch (_) {
                // 忽略无法解析的事件
              }
            }
            buffer.clear();
          }
        } else if (!line.startsWith(':')) {
          buffer.add(line);
        }
      }
    } finally {
      client.close();
    }
  }
}
