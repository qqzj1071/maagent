import 'dart:async';

import 'package:flutter/material.dart';

import '../api.dart';
import '../models.dart';
import '../store.dart';
import 'login_page.dart';
import 'reports_page.dart';

class HomePage extends StatefulWidget {
  const HomePage({super.key});

  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage>
    with SingleTickerProviderStateMixin {
  late final TabController _tabs = TabController(length: 3, vsync: this);
  late final ApiClient _api =
      ApiClient(baseUrl: AppStore.baseUrl, token: AppStore.token);
  final ScrollController _logScroll = ScrollController();

  WorkflowStatus _status = WorkflowStatus();
  final List<LogLine> _logs = [];
  final List<ReportSummary> _reports = [];
  StreamSubscription<Map<String, dynamic>>? _sub;
  Timer? _retry;
  bool _loading = true;
  bool _busy = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    _refresh();
    _subscribe();
  }

  @override
  void dispose() {
    _sub?.cancel();
    _retry?.cancel();
    _logScroll.dispose();
    _tabs.dispose();
    super.dispose();
  }

  Future<void> _refresh() async {
    try {
      final status = await _api.status();
      final logs = await _api.logs(limit: 200);
      final reports = await _api.reports(limit: 30);
      if (!mounted) return;
      setState(() {
        _status = WorkflowStatus.fromJson(
            (status['status'] as Map?)?.cast<String, dynamic>() ?? {});
        _logs
          ..clear()
          ..addAll(logs.map(
              (e) => LogLine.fromJson((e as Map).cast<String, dynamic>())));
        _reports
          ..clear()
          ..addAll(reports.map(
              (e) => ReportSummary.fromJson((e as Map).cast<String, dynamic>())));
        _loading = false;
        _error = null;
      });
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _error = e.message;
      });
    }
  }

  void _subscribe() {
    _sub?.cancel();
    _sub = _api.events().listen(
          _onEvent,
          onError: (_) => _scheduleRetry(),
          onDone: _scheduleRetry,
        );
  }

  void _scheduleRetry() {
    if (!mounted) return;
    _retry?.cancel();
    _retry = Timer(const Duration(seconds: 3), () {
      if (mounted) _subscribe();
    });
  }

  void _onEvent(Map<String, dynamic> event) {
    if (!mounted) return;
    final type = event['type']?.toString();
    final data = event['data'];
    if (data is! Map) return;
    final map = data.cast<String, dynamic>();
    setState(() {
      if (type == 'log') {
        _logs.add(LogLine.fromJson(map));
        if (_logs.length > 500) {
          _logs.removeRange(0, _logs.length - 500);
        }
      } else if (type == 'report') {
        _reports.insert(0, ReportSummary.fromJson(map));
        if (_reports.length > 30) _reports.removeLast();
      } else if (type == 'status' || type == 'snapshot') {
        _status = WorkflowStatus.fromJson(map);
        _loading = false;
      }
    });
    if (type == 'log') _scrollLogsToEnd();
  }

  void _scrollLogsToEnd() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_logScroll.hasClients) {
        _logScroll.jumpTo(_logScroll.position.maxScrollExtent);
      }
    });
  }

  Future<void> _start() async {
    setState(() => _busy = true);
    try {
      final data = await _api.startWorkflow();
      final status = (data['status'] as Map?)?.cast<String, dynamic>();
      if (mounted && status != null) {
        setState(() => _status = WorkflowStatus.fromJson(status));
      }
    } on ApiException catch (e) {
      _snack(e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _stop() async {
    setState(() => _busy = true);
    try {
      final data = await _api.stopWorkflow();
      final status = (data['status'] as Map?)?.cast<String, dynamic>();
      if (mounted && status != null) {
        setState(() => _status = WorkflowStatus.fromJson(status));
      }
    } on ApiException catch (e) {
      _snack(e.message);
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Future<void> _logout() async {
    try {
      await _api.logout();
    } catch (_) {
      // 忽略登出失败，本地照样清除
    }
    await AppStore.clearSession();
    if (!mounted) return;
    Navigator.of(context).pushAndRemoveUntil(
      MaterialPageRoute(builder: (_) => const LoginPage()),
      (route) => false,
    );
  }

  void _snack(String message) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(message)));
  }

  String _statusText(WorkflowStatus status) {
    switch (status.status) {
      case 'running':
        return '运行中';
      case 'stopping':
        return '停止中';
      default:
        return '空闲';
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Maagent'),
        actions: [
          IconButton(onPressed: _refresh, icon: const Icon(Icons.refresh)),
          IconButton(onPressed: _logout, icon: const Icon(Icons.logout)),
        ],
        bottom: TabBar(
          controller: _tabs,
          tabs: const [
            Tab(text: '状态'),
            Tab(text: '日志'),
            Tab(text: '报告'),
          ],
        ),
      ),
      body: _loading
          ? const Center(child: CircularProgressIndicator())
          : TabBarView(
              controller: _tabs,
              children: [_statusTab(), _logsTab(), _reportsTab()],
            ),
    );
  }

  Widget _statusTab() {
    final status = _status;
    return RefreshIndicator(
      onRefresh: _refresh,
      child: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          if (_error != null)
            Card(
              color: Colors.red.shade50,
              child: ListTile(
                title: Text(_error!, style: const TextStyle(color: Colors.red)),
                subtitle: const Text('下拉刷新重试'),
              ),
            ),
          Card(
            child: Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Icon(
                        status.running ? Icons.play_circle : Icons.pause_circle,
                        color: status.running ? Colors.green : Colors.grey,
                      ),
                      const SizedBox(width: 8),
                      Text(
                        _statusText(status),
                        style: const TextStyle(
                            fontSize: 20, fontWeight: FontWeight.bold),
                      ),
                    ],
                  ),
                  const SizedBox(height: 8),
                  if (status.message.isNotEmpty) Text(status.message),
                  if (status.current != null)
                    Text('当前：${status.current}（${status.index}/${status.total}）'),
                  if (status.startedAt.isNotEmpty) Text('开始：${status.startedAt}'),
                  if (status.finishedAt.isNotEmpty) Text('结束：${status.finishedAt}'),
                  if (status.nextRun != null) Text('下次定时：${status.nextRun}'),
                ],
              ),
            ),
          ),
          const SizedBox(height: 16),
          Row(
            children: [
              Expanded(
                child: FilledButton.icon(
                  onPressed: (_busy || status.isBusy) ? null : _start,
                  icon: const Icon(Icons.play_arrow),
                  label: const Text('启动日常'),
                ),
              ),
              const SizedBox(width: 12),
              Expanded(
                child: OutlinedButton.icon(
                  onPressed: (_busy || !status.isBusy) ? null : _stop,
                  icon: const Icon(Icons.stop),
                  label: const Text('停止'),
                  style: OutlinedButton.styleFrom(foregroundColor: Colors.red),
                ),
              ),
            ],
          ),
          const SizedBox(height: 24),
          const Text('任务清单', style: TextStyle(fontWeight: FontWeight.bold)),
          const SizedBox(height: 8),
          ...status.software.map(
            (item) => Card(
              child: ListTile(
                leading: Icon(
                  item['enabled'] == true
                      ? Icons.check_circle
                      : Icons.remove_circle_outline,
                  color: item['enabled'] == true ? Colors.green : Colors.grey,
                ),
                title: Text(item['software']?.toString() ?? ''),
                subtitle: Text(item['enabled'] == true ? '已启用' : '已停用'),
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _logsTab() {
    if (_logs.isEmpty) {
      return const Center(child: Text('暂无日志'));
    }
    return ListView.builder(
      controller: _logScroll,
      padding: const EdgeInsets.all(12),
      itemCount: _logs.length,
      itemBuilder: (_, index) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 2),
        child: Text(
          _logs[index].display,
          style: const TextStyle(fontFamily: 'monospace', fontSize: 12),
        ),
      ),
    );
  }

  Widget _reportsTab() {
    if (_reports.isEmpty) {
      return const Center(child: Text('暂无报告'));
    }
    return RefreshIndicator(
      onRefresh: _refresh,
      child: ListView.builder(
        padding: const EdgeInsets.all(12),
        itemCount: _reports.length,
        itemBuilder: (_, index) {
          final report = _reports[index];
          return Card(
            child: ListTile(
              title: Text('${report.game}  ${report.statusLabel}'),
              subtitle: Text(
                  '${report.startedAt} ~ ${report.finishedAt}\n${report.duration}'),
              isThreeLine: true,
              onTap: () => Navigator.of(context).push(
                MaterialPageRoute(
                  builder: (_) =>
                      ReportDetailPage(api: _api, reportId: report.id),
                ),
              ),
            ),
          );
        },
      ),
    );
  }
}
