class Account {
  final int id;
  final String email;
  final String? phone;
  final bool emailVerified;
  final String createdAt;

  Account({
    required this.id,
    required this.email,
    this.phone,
    this.emailVerified = false,
    this.createdAt = '',
  });

  factory Account.fromJson(Map<String, dynamic> json) => Account(
        id: (json['id'] as num?)?.toInt() ?? 0,
        email: json['email'] as String? ?? '',
        phone: json['phone'] as String?,
        emailVerified: json['email_verified'] as bool? ?? false,
        createdAt: json['created_at'] as String? ?? '',
      );
}

class WorkflowStatus {
  final String status;
  final bool running;
  final List<String> softwareList;
  final int index;
  final int total;
  final String? current;
  final String message;
  final String startedAt;
  final String finishedAt;
  final String? nextRun;
  final List<Map<String, dynamic>> software;
  final Map<String, dynamic>? latestReport;

  WorkflowStatus({
    this.status = 'idle',
    this.running = false,
    this.softwareList = const [],
    this.index = 0,
    this.total = 0,
    this.current,
    this.message = '',
    this.startedAt = '',
    this.finishedAt = '',
    this.nextRun,
    this.software = const [],
    this.latestReport,
  });

  factory WorkflowStatus.fromJson(Map<String, dynamic> json) {
    final list = (json['software_list'] as List?) ?? const [];
    final software = (json['software'] as List?) ?? const [];
    return WorkflowStatus(
      status: json['status'] as String? ?? 'idle',
      running: json['running'] as bool? ?? false,
      softwareList: list.map((e) => e.toString()).toList(),
      index: (json['index'] as num?)?.toInt() ?? 0,
      total: (json['total'] as num?)?.toInt() ?? 0,
      current: json['current'] as String?,
      message: json['message'] as String? ?? '',
      startedAt: json['started_at'] as String? ?? '',
      finishedAt: json['finished_at'] as String? ?? '',
      nextRun: json['next_run'] as String?,
      software: software
          .whereType<Map>()
          .map((e) => e.cast<String, dynamic>())
          .toList(),
      latestReport: (json['latest_report'] as Map?)?.cast<String, dynamic>(),
    );
  }

  bool get isBusy => running || status == 'stopping';
}

class ReportSummary {
  final String id;
  final String game;
  final String status;
  final String statusLabel;
  final String startedAt;
  final String finishedAt;
  final String duration;
  final List<String> errors;
  final List<String> warnings;
  final String sanity;
  final String nextDeadline;
  final String text;

  ReportSummary({
    required this.id,
    this.game = '',
    this.status = 'unknown',
    this.statusLabel = '',
    this.startedAt = '',
    this.finishedAt = '',
    this.duration = '',
    this.errors = const [],
    this.warnings = const [],
    this.sanity = '',
    this.nextDeadline = '',
    this.text = '',
  });

  factory ReportSummary.fromJson(Map<String, dynamic> json) => ReportSummary(
        id: json['id'] as String? ?? '',
        game: json['game'] as String? ?? '',
        status: json['status'] as String? ?? 'unknown',
        statusLabel: json['status_label'] as String? ?? '',
        startedAt: json['started_at'] as String? ?? '',
        finishedAt: json['finished_at'] as String? ?? '',
        duration: json['duration'] as String? ?? '',
        errors: ((json['errors'] as List?) ?? const []).map((e) => e.toString()).toList(),
        warnings:
            ((json['warnings'] as List?) ?? const []).map((e) => e.toString()).toList(),
        sanity: json['sanity'] as String? ?? '',
        nextDeadline: json['next_deadline'] as String? ?? '',
        text: json['text'] as String? ?? '',
      );
}

class LogLine {
  final String time;
  final String level;
  final String message;

  LogLine({this.time = '', this.level = 'INFO', this.message = ''});

  factory LogLine.fromJson(Map<String, dynamic> json) => LogLine(
        time: json['time'] as String? ?? '',
        level: json['level'] as String? ?? 'INFO',
        message: json['message'] as String? ?? '',
      );

  String get display => time.isEmpty ? message : '$time  $message';
}
