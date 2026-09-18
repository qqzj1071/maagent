import 'package:flutter/material.dart';

import '../api.dart';
import '../models.dart';

class ReportDetailPage extends StatefulWidget {
  const ReportDetailPage({super.key, required this.api, required this.reportId});

  final ApiClient api;
  final String reportId;

  @override
  State<ReportDetailPage> createState() => _ReportDetailPageState();
}

class _ReportDetailPageState extends State<ReportDetailPage> {
  Map<String, dynamic>? _report;
  String? _error;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final report = await widget.api.reportDetail(widget.reportId);
      if (!mounted) return;
      setState(() => _report = report);
    } on ApiException catch (e) {
      if (!mounted) return;
      setState(() => _error = e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final report = _report;
    return Scaffold(
      appBar: AppBar(title: const Text('任务报告')),
      body: _error != null
          ? Center(child: Text(_error!, style: const TextStyle(color: Colors.red)))
          : report == null
              ? const Center(child: CircularProgressIndicator())
              : ListView(
                  padding: const EdgeInsets.all(16),
                  children: [
                    Text(
                      '${report['game'] ?? ''}  ${report['status_label'] ?? ''}',
                      style: const TextStyle(
                          fontSize: 18, fontWeight: FontWeight.bold),
                    ),
                    const SizedBox(height: 12),
                    _row('开始', report['started_at']),
                    _row('结束', report['finished_at']),
                    _row('耗时', report['duration']),
                    _row('剩余理智', report['sanity']),
                    _row('下次最晚开始', report['next_deadline']),
                    _row('剿灭作战', report['annihilation']),
                    _row('月常购买', report['monthly']),
                    const Divider(height: 28),
                    const Text('详情', style: TextStyle(fontWeight: FontWeight.bold)),
                    const SizedBox(height: 8),
                    SelectableText(report['text']?.toString() ?? ''),
                  ],
                ),
    );
  }

  Widget _row(String label, Object? value) {
    final text = value?.toString() ?? '';
    if (text.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 4),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          SizedBox(
            width: 96,
            child: Text(label, style: const TextStyle(color: Colors.grey)),
          ),
          Expanded(child: SelectableText(text)),
        ],
      ),
    );
  }
}
