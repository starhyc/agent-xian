#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import random
import socket
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.error import URLError
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import urlopen


HOST = "127.0.0.1"
PORT = int(os.environ.get("WIKI_PORT", "18089"))
SERVICE_VERSION = "2026.06-linked-faq-pages-shuffled"


PAGES: list[dict[str, Any]] = [
    {
        "id": "WKP-6EB4668E0118DF68FA9C79A7A8055D96",
        "title": "登录失败当前处理流程",
        "overview": "登录失败、login failure、auth session、session.json、login_state.json、禁止删除整个配置目录",
        "path": "/wiki/ide-plugin/login/current",
        "updated_at": "2026-05-25 09:00:00",
        "keywords": ["登录失败", "登录", "重登", "~/.devpilot", "session.json", "login_state.json", "删除目录", "清理缓存"],
        "faqs": [
            {
                "id": "login_allowed_cache_files",
                "q": "登录失败或登录态异常时允许清理哪些文件？",
                "a": "不要直接删除整个 ~/.devpilot 目录；只允许清理 ~/.devpilot/auth/session.json 和 ~/.devpilot/cache/login_state.json。",
            },
        ],
    },
    {
        "id": "WKP-58E4EC671CABC84F2A097161A0A599C2",
        "title": "代码补全无响应排障流程",
        "overview": "代码补全无响应、code completion no response、completion-service、traceId、gateway status code",
        "path": "/wiki/ide-plugin/completion/current",
        "updated_at": "2026-05-24 14:00:00",
        "keywords": ["代码补全", "补全无响应", "completion", "completion-service", "traceId", "模型网关", "429", "503", "排障"],
        "faqs": [
            {
                "id": "completion_no_response",
                "q": "代码补全无响应时应该先做哪些排查？",
                "a": "检查插件状态面板中的 completion-service 是否 running；查看最近一次 completion 请求 traceId；检查模型网关返回码：401、429、503；不要直接让用户重装插件。",
            },
            {
                "id": "gateway_429",
                "q": "模型网关返回 429 代表什么？",
                "a": "429：配额不足或限流。",
            },
            {
                "id": "gateway_503",
                "q": "模型网关返回 503 应该怎么处理？",
                "a": "503：模型服务不可用，需要升级平台值班。",
            },
        ],
    },
    {
        "id": "WKP-032159B6B881574B0D44524B70C4CDA6",
        "title": "MCP 工具连接失败处理流程",
        "overview": "MCP 连接失败、MCP tool connection failed、mcp.json、MCP server、stdio/http mode",
        "path": "/wiki/ide-plugin/mcp/current",
        "updated_at": "2026-05-23 10:00:00",
        "keywords": ["MCP", "mcp.json", "连接失败", "server 配置", "stdio", "http", "工具连接"],
        "faqs": [
            {
                "id": "mcp_connection_failed",
                "q": "MCP 工具连接失败时如何排查？",
                "a": "检查 mcp.json 是否存在；检查 MCP server 配置路径是否正确；区分 stdio 和 http 模式；不要整体关闭 MCP 功能。",
            },
        ],
    },
    {
        "id": "WKP-6ED0FE77083E3BA5B13E337259EFE760",
        "title": "插件日志路径与日志收集规范",
        "overview": "插件日志路径、plugin logs path、Windows、macOS、Linux、Remote Host 日志差异",
        "path": "/wiki/ide-plugin/logs/latest",
        "updated_at": "2026-05-25 09:30:00",
        "keywords": ["日志路径", "日志", "logs", "Windows", "macOS", "Linux", "收集日志", "路径"],
        "faqs": [
            {
                "id": "plugin_log_paths",
                "q": "不同操作系统的插件日志路径在哪里？",
                "a": "Windows：%APPDATA%/DevPilot/logs；macOS：~/Library/Logs/DevPilot；Linux：~/.config/devpilot/logs。",
            },
        ],
    },
    {
        "id": "WKP-ACFF0652238C4BF8EA51F3907447AEB8",
        "title": "诊断日志上报接口 v3",
        "overview": "诊断日志上传接口、diagnostics upload API、multipart/form-data、diagnosticFile、traceId",
        "path": "/wiki/ide-plugin/diagnostics/upload-v3",
        "updated_at": "2026-05-22 10:00:00",
        "keywords": ["诊断", "日志上报", "上报接口", "diagnosticFile", "multipart", "traceId", "upload", "字段"],
        "faqs": [
            {
                "id": "diagnostics_upload_v3",
                "q": "诊断日志应该通过哪个接口上传？",
                "a": "POST /api/ide-plugin/v3/diagnostics/upload；请求类型：multipart/form-data；字段：diagnosticFile、employeeId、ideType、pluginVersion、traceId。",
            },
        ],
    },
    {
        "id": "WKP-765F951A5407173B0AB22C30E05AEC3A",
        "title": "灰度版本故障回滚策略",
        "overview": "灰度名单、canary rollout、beta crash、feature flags、rollback v2.7.4",
        "path": "/wiki/ide-plugin/rollout/current",
        "updated_at": "2026-05-25 13:00:00",
        "keywords": ["灰度", "崩溃", "回滚", "回退", "v2.8.0-beta.3", "v2.7.4", "toolPlanner", "streamingV2"],
        "faqs": [
            {
                "id": "rollout_crash_rollback",
                "q": "灰度版本崩溃时如何处理和回滚？",
                "a": "先确认用户是否在灰度名单；优先关闭 agent.experimental.toolPlanner=false 和 completion.streamingV2=false；仍崩溃再回退到 v2.7.4。",
            },
        ],
    },
    {
        "id": "WKP-A0E12589C9F39EFDDF6B0DF4FD2680E7",
        "title": "本地索引失败处理说明",
        "overview": "本地索引 building、local index、workspace_index、index.lock、metadata.db、旧口径",
        "path": "/wiki/ide-plugin/index/20260520",
        "updated_at": "2026-05-20 10:00:00",
        "keywords": ["本地索引", "索引", "building", "workspace_index", "重建索引", "索引失败"],
        "faqs": [
            {
                "id": "index_building_old",
                "q": "本地索引一直 building 时如何处理？",
                "a": "索引一直 building 时，可先尝试重启插件后台服务；如果仍失败，可以删除 workspace_index 后重建。",
            },
        ],
    },
    {
        "id": "WKP-3E20AF6B520AA5DA5DB9998C1D38723C",
        "title": "企业代理和证书处理说明",
        "overview": "企业代理异常、enterprise proxy、certificate chain、IDE 进程代理继承、旧口径",
        "path": "/wiki/ide-plugin/proxy/20260518",
        "updated_at": "2026-05-18 10:00:00",
        "keywords": ["企业代理", "证书", "安全证书", "关闭代理", "代理异常", "证书信任链"],
        "faqs": [
            {
                "id": "proxy_cert_old",
                "q": "企业代理和证书异常时如何处理？",
                "a": "企业代理异常时，可先让用户临时关闭代理后重试。",
            },
        ],
    },
    {
        "id": "WKP-59CCCF881598180E7CA9B1DFF61ECE25",
        "title": "DevPilot IDE 插件安装规范",
        "overview": "企业插件市场、plugin marketplace、VS Code .vsix、JetBrains .zip、Trae 安装入口",
        "path": "/wiki/ide-plugin/install/current",
        "updated_at": "2026-05-25 10:00:00",
        "keywords": ["安装", "企业插件市场", ".vsix", ".zip", "VS Code", "JetBrains", "Trae", "离线包", "手动解压", "插件目录"],
        "faqs": [
            {
                "id": "install_channels",
                "q": "DevPilot IDE 插件如何安装？",
                "a": "优先通过企业插件市场安装；VS Code 使用 .vsix 离线包；JetBrains 使用 .zip 插件包；Trae 通过企业插件市场安装。",
            },
            {
                "id": "install_no_manual_extract",
                "q": "能不能手动解压插件包到 IDE 插件目录？",
                "a": "不要手动解压到 IDE 插件目录；必须通过企业插件市场或 IDE 官方插件安装入口安装。",
            },
        ],
    },
    {
        "id": "WKP-089CA1A1C25C139800A5164D0A3EE9AD",
        "title": "插件版本升级与灰度版本说明",
        "overview": "插件版本选择、stable v2.7.4、beta v2.8.0-beta.3、灰度名单",
        "path": "/wiki/ide-plugin/upgrade/current",
        "updated_at": "2026-05-25 10:20:00",
        "keywords": ["升级", "版本", "稳定版", "灰度名单", "v2.7.4", "v2.8.0-beta.3", "beta"],
        "faqs": [
            {
                "id": "upgrade_version_selection",
                "q": "普通用户和灰度用户应该安装哪个版本？",
                "a": "普通用户默认安装稳定版 v2.7.4；只有灰度名单用户才安装 v2.8.0-beta.3。",
            },
        ],
    },
    {
        "id": "WKP-DE78181BA74AE264DB6464D61FD0B932",
        "title": "插件卸载与重装安全规范",
        "overview": "插件卸载重装、reinstall safety、config backup、不要删除整个用户配置目录",
        "path": "/wiki/ide-plugin/uninstall/current",
        "updated_at": "2026-05-25 12:00:00",
        "keywords": ["卸载", "重装", "用户配置", "配置目录", "备份", "导出", "重装插件", "删除整个", "~/.devpilot", "不是第一步", "先导出", "自定义配置"],
        "faqs": [
            {
                "id": "uninstall_before_reinstall",
                "q": "插件卸载或重装前应该注意什么？",
                "a": "重装插件不是第一步；不要删除整个用户配置目录；先导出或备份用户自定义配置。",
            },
        ],
    },
    {
        "id": "WKP-4F43582BED769B54C85D9B76C1E691FF",
        "title": "DevPilot IDE 插件问题提单规范",
        "overview": "问题提单规范、support ticket required fields、employeeId、traceId、diagnosticId、复现步骤",
        "path": "/wiki/ide-plugin/ticket/current",
        "updated_at": "2026-05-25 11:00:00",
        "keywords": ["提单", "必填信息", "employeeId", "IDE 类型", "操作系统", "插件版本", "traceId", "diagnosticId", "复现步骤", "群里", "口头反馈", "研发支持平台"],
        "faqs": [
            {
                "id": "ticket_required_fields",
                "q": "提交 DevPilot IDE 插件问题单需要哪些必填信息？",
                "a": "提单必须包含 employeeId、IDE 类型、IDE 版本、操作系统、插件版本、问题类型、失败时间、traceId、是否灰度用户、是否使用企业代理、诊断包 diagnosticId、复现步骤、期望行为、实际行为。",
            },
            {
                "id": "ticket_channel",
                "q": "正式问题能不能只在群里口头反馈？",
                "a": "正式问题必须通过企业研发支持平台提单，不能只在群里口头反馈。",
            },
            {
                "id": "ticket_trace_diag_only",
                "q": "只有 traceId 和 diagnosticId 能不能直接提单？",
                "a": "不能只靠 traceId 和 diagnosticId 直接提交；还需要补齐 employeeId、IDE 类型和版本、操作系统、插件版本、失败时间、复现步骤、期望行为和实际行为。",
            },
        ],
    },
    {
        "id": "WKP-559A57B69FCE2F856CD47A061F3DFF13",
        "title": "旧版日志上报接口",
        "overview": "旧版 /api/plugin/report、legacy report API、file、userId、pluginVersion",
        "path": "/wiki/archive/plugin-report-api-v1",
        "updated_at": "2026-04-10 09:00:00",
        "keywords": ["旧接口", "旧版日志", "/api/plugin/report", "file", "userId", "pluginVersion"],
        "faqs": [
            {
                "id": "legacy_report_api_v1",
                "q": "旧版日志上报接口是什么？",
                "a": "POST /api/plugin/report；字段：file、userId、pluginVersion。",
            },
        ],
    },
    {
        "id": "WKP-220E4EE12DE068039945C008C13153D0",
        "title": "旧版清理缓存方案",
        "overview": "旧版清理缓存方案、legacy cache cleanup、delete ~/.devpilot、历史归档",
        "path": "/wiki/archive/clear-cache-old",
        "updated_at": "2026-04-18 10:00:00",
        "keywords": ["旧版清理", "删除整个", "~/.devpilot", "缓存", "登录失败", "补全失败", "Agent 失败"],
        "faqs": [
            {
                "id": "old_clear_cache_all",
                "q": "旧版缓存清理方案是什么？",
                "a": "登录失败、补全失败或 Agent 失败时，可以删除整个 ~/.devpilot 目录后重新登录。",
            },
        ],
    },
    {
        "id": "WKP-5A0D9650E7486821AC8C59CEECC8B123",
        "title": "CodeReviewBot 插件服务规范",
        "overview": "CodeReviewBot、code review plugin、negative feedback、与 DevPilot IDE 插件不同",
        "path": "/wiki/other/codereviewbot/service",
        "updated_at": "2026-05-29 16:00:00",
        "keywords": ["CodeReviewBot", "代码评审", "差评", "服务复盘", "other_product"],
        "faqs": [
            {
                "id": "codereviewbot_negative_feedback",
                "q": "CodeReviewBot 用户负面反馈如何处理？",
                "a": "用户负面情绪明显且长时间未补充日志时，先说明已保留上下文并提醒补充；如果影响面扩大或无法安抚，再升级运营代表或 SRE Leader。",
            },
        ],
    },
    {
        "id": "WKP-32B2E01C2935FC5D46356EBF55B8BADA",
        "title": "插件商城问题单处理规范",
        "overview": "插件商城、plugin marketplace ticket、diagnostic package、与 DevPilot IDE 插件支撑流程不同",
        "path": "/wiki/other/plugin-market/ticket",
        "updated_at": "2026-05-29 16:03:00",
        "keywords": ["插件商城", "工单", "日志", "诊断包", "关单", "other_product"],
        "faqs": [
            {
                "id": "plugin_market_no_conclusion",
                "q": "插件商城问题没有结论时能否关单？",
                "a": "尚缺日志或诊断包且没有明确结论时，不宜直接关单，应提醒用户补充定位信息。",
            },
        ],
    },
    {
        "id": "WKP-D6EE35A4E16CD77E8F4F16395BB6D318",
        "title": "浏览器插件规则订阅说明",
        "overview": "浏览器插件、browser extension、rule subscription、与 IDE 插件无关",
        "path": "/wiki/other/browser-extension/rules",
        "updated_at": "2026-05-28 18:00:00",
        "keywords": ["浏览器插件", "广告拦截", "规则订阅", "other_product"],
        "faqs": [
            {
                "id": "browser_extension_subscription",
                "q": "浏览器广告拦截插件如何更新规则订阅？",
                "a": "在浏览器插件设置页手动刷新规则订阅，确认订阅源可访问后再观察拦截效果。",
            },
        ],
    },
    {
        "id": "WKP-09626A043865A59192B2DFEFDA9C2257",
        "title": "IDE 主题偏好配置建议",
        "overview": "IDE theme color、light/dark theme、个人偏好配置、与插件故障处理无关",
        "path": "/wiki/other/ide-theme/preference",
        "updated_at": "2026-05-23 10:00:00",
        "keywords": ["IDE 主题", "主题颜色", "浅色", "深色", "偏好"],
        "faqs": [
            {
                "id": "ide_theme_choice",
                "q": "IDE 主题颜色应该如何选择？",
                "a": "浅色主题适合白天，深色主题适合夜间，最终以个人阅读偏好为准。",
            },
        ],
    },
    {
        "id": "WKP-D8D3D74BB62758B4275AF9B6B5C353C8",
        "title": "SSO 登录异常旧版处理记录",
        "overview": "SSO login issue、browser cookie、legacy auth cache、历史归档口径",
        "path": "/wiki/archive/login/sso-cache-legacy",
        "updated_at": "2026-04-21 11:00:00",
        "keywords": ["登录", "SSO", "缓存", "~/.devpilot", "旧版", "归档"],
        "faqs": [
            {
                "id": "sso_cache_legacy",
                "q": "旧版 SSO 登录异常时曾经如何清理缓存？",
                "a": "旧版 SSO 登录异常时曾建议清理本地登录缓存后重启 IDE，该口径只适用于 2026-04 的 SSO 试点版本。",
            },
            {
                "id": "sso_browser_cookie",
                "q": "SSO 浏览器 Cookie 异常时如何处理？",
                "a": "SSO 浏览器 Cookie 异常时先退出企业账号中心并重新授权，不直接删除 DevPilot 插件配置目录。",
            },
        ],
    },
    {
        "id": "WKP-7B55FD3896B705543E111A091781F96F",
        "title": "华东代理场景补全异常复盘",
        "overview": "华东代理补全复盘、east proxy review、enterprise proxy、certificate chain、completion no response",
        "path": "/wiki/archive/completion/east-proxy-review",
        "updated_at": "2026-05-29 09:40:00",
        "keywords": ["代码补全", "补全无响应", "企业代理", "证书链", "华东", "灰度"],
        "faqs": [
            {
                "id": "east_proxy_completion_scope",
                "q": "华东灰度代理场景下补全无响应如何定位？",
                "a": "华东灰度用户同时出现企业代理异常和补全无响应时，应额外检查企业证书链和代理继承策略，该口径不适用于普通补全无响应问题。",
            },
            {
                "id": "proxy_disable_draft",
                "q": "能否临时关闭企业代理来恢复补全？",
                "a": "安全评审前曾讨论过临时关闭企业代理，但该方案没有进入正式处理流程，不能作为用户指导口径。",
            },
        ],
    },
    {
        "id": "WKP-3CB325900588F811274A5EF13A233381",
        "title": "MCP 连接失败旧版处置归档",
        "overview": "MCP disable old handling、关闭 MCP、旧版本试点口径",
        "path": "/wiki/archive/mcp/disable-old",
        "updated_at": "2026-05-08 16:10:00",
        "keywords": ["MCP", "连接失败", "关闭 MCP", "旧版", "试点"],
        "faqs": [
            {
                "id": "mcp_disable_old",
                "q": "旧版 MCP 连接失败时是否关闭全部 MCP？",
                "a": "旧版试点曾建议临时关闭 MCP 能力以保证补全可用，但该建议已归档，不适用于当前 MCP 排障流程。",
            }
        ],
    },
    {
        "id": "WKP-529DD6FDFF49F7769979B9BE676EF39C",
        "title": "Remote Workspace 日志定位说明",
        "overview": "Remote Workspace logs、Remote Host、远端日志路径、本地日志路径差异",
        "path": "/wiki/ide-plugin/logs/remote-workspace",
        "updated_at": "2026-05-28 18:30:00",
        "keywords": ["日志", "Remote Workspace", "Remote Host", "远程工作区", "Linux", "路径"],
        "faqs": [
            {
                "id": "remote_workspace_logs",
                "q": "远程工作区的日志应优先在哪里查看？",
                "a": "远程工作区场景应同时查看本地 IDE 日志和 Remote Host 日志，不能只按本地插件日志路径判断。",
            },
            {
                "id": "remote_logs_not_local_only",
                "q": "Remote Host 日志能否替代本地插件日志？",
                "a": "Remote Host 日志不能替代本地插件日志；两侧日志需要按 traceId 和失败时间共同关联。",
            },
        ],
    },
    {
        "id": "WKP-D50237DA1B4542F3B7937F104519A97C",
        "title": "诊断包隐私与脱敏评审",
        "overview": "diagnostic package privacy、token redaction、cookie redaction、source snippet filtering",
        "path": "/wiki/ide-plugin/diagnostics/privacy-review",
        "updated_at": "2026-05-26 11:05:00",
        "keywords": ["诊断包", "隐私", "脱敏", "token", "cookie", "accessKey", "源码片段"],
        "faqs": [
            {
                "id": "diagnostic_privacy_scope",
                "q": "诊断包上传前需要过滤哪些敏感信息？",
                "a": "诊断包上传前需要过滤 token、cookie、accessKey、refreshToken、源码片段和个人敏感信息，再说明材料仅用于故障定位。",
            }
        ],
    },
    {
        "id": "WKP-F435F5609CDC2E7DB14510B60C5FA554",
        "title": "灰度版本回滚历史方案",
        "overview": "beta rollback history、feature flags、wait next release、历史回滚策略",
        "path": "/wiki/archive/rollout/beta-history",
        "updated_at": "2026-05-12 10:00:00",
        "keywords": ["灰度", "beta", "崩溃", "回滚", "v2.8.0-beta.3", "实验开关"],
        "faqs": [
            {
                "id": "beta_history_wait_release",
                "q": "灰度 beta 崩溃历史上是否等待下个版本统一修复？",
                "a": "早期灰度复盘曾建议等待下个版本统一修复，但该建议不适合作为当前用户侧故障恢复口径。",
            },
            {
                "id": "beta_toggle_history",
                "q": "灰度实验开关历史口径是什么？",
                "a": "历史灰度开关说明只记录实验能力名称，实际回滚顺序应以当前灰度版本故障回滚策略为准。",
            },
        ],
    },
    {
        "id": "WKP-5C0E7ABFB513050398FC66EFEAB66C94",
        "title": "远程工作区索引 building 处理说明",
        "overview": "Remote Workspace index、Remote Host、remote index directory、workspace_index 差异",
        "path": "/wiki/ide-plugin/index/remote-host",
        "updated_at": "2026-05-29 12:10:00",
        "keywords": ["索引", "building", "远程工作区", "Remote Host", "workspace_index", "远端索引"],
        "faqs": [
            {
                "id": "remote_index_building",
                "q": "远程工作区索引一直 building 时如何处理？",
                "a": "远程索引 building 时应确认 Remote Host 插件后台进程、远端工作区权限和远端索引目录，再重启 IDE Remote Host。",
            },
            {
                "id": "remote_index_local_cache",
                "q": "远程索引问题能否直接清空本地 workspace_index？",
                "a": "远程工作区索引问题不能直接按本地 workspace_index 清理处理，必须先区分远端和本地索引位置。",
            },
        ],
    },
    {
        "id": "WKP-49A562C4CDCA90E88FB3EB8B6152A570",
        "title": "企业代理安全评审试点记录",
        "overview": "enterprise proxy security pilot、certificate trust chain、安全评审、试点用户",
        "path": "/wiki/archive/proxy/security-pilot",
        "updated_at": "2026-05-29 09:40:00",
        "keywords": ["企业代理", "证书", "证书信任链", "安全评审", "试点"],
        "faqs": [
            {
                "id": "proxy_security_pilot",
                "q": "企业代理安全评审试点记录适用于所有代理异常吗？",
                "a": "企业代理安全评审试点记录只适用于试点用户，不能替代正式代理异常处理口径。",
            },
            {
                "id": "proxy_disable_not_allowed",
                "q": "代理异常时能否要求用户关闭企业代理？",
                "a": "代理异常不能直接要求用户关闭企业代理，必须先确认企业代理配置和证书策略是否被 IDE 进程继承。",
            },
        ],
    },
    {
        "id": "WKP-8F720FC73D19703F44AFADCC329B8B3D",
        "title": "手动解压安装旧方案归档",
        "overview": "manual extract install、plugin directory、zip、旧版安装方式归档",
        "path": "/wiki/archive/install/manual-extract",
        "updated_at": "2026-04-15 12:00:00",
        "keywords": ["安装", "手动解压", "插件目录", "zip", "旧版"],
        "faqs": [
            {
                "id": "manual_extract_old",
                "q": "旧版插件包是否支持手动解压安装？",
                "a": "旧版插件包曾支持手动解压到 IDE 插件目录，但当前企业发布流程不再采用该安装方式。",
            }
        ],
    },
    {
        "id": "WKP-1EB5BC3B9084E2E0A84FBB8358BC08FE",
        "title": "轻量提单字段草案",
        "overview": "lightweight ticket draft、traceId、diagnosticId、字段不完整",
        "path": "/wiki/archive/ticket/lightweight-draft",
        "updated_at": "2026-05-18 14:00:00",
        "keywords": ["提单", "traceId", "diagnosticId", "字段", "草案"],
        "faqs": [
            {
                "id": "ticket_lightweight_draft",
                "q": "轻量提单草案是否只要求 traceId 和 diagnosticId？",
                "a": "轻量提单草案曾只要求 traceId 和 diagnosticId，但该草案字段不足，不能作为正式问题单提交规范。",
            },
            {
                "id": "ticket_missing_context",
                "q": "缺少复现步骤时能否直接提交问题单？",
                "a": "缺少复现步骤时可以先保存草稿，但正式提交前仍需补齐失败时间、期望行为和实际行为。",
            },
        ],
    },
    {
        "id": "WKP-E41AB5DE00167AEE7E7EF35E38F014BE",
        "title": "FSE 建群协同旧节奏记录",
        "overview": "FSE group sync、owner、progress update、旧版同步节奏",
        "path": "/wiki/archive/fse/group-sync",
        "updated_at": "2026-05-20 17:00:00",
        "keywords": ["建群", "协同", "同步", "FSE", "进展"],
        "faqs": [
            {
                "id": "group_sync_two_hours",
                "q": "旧版建群协同多久同步一次进展？",
                "a": "旧版建群协同记录要求每2小时同步一次进展，该节奏已经不作为当前高优先级问题协同口径。",
            }
        ],
    },
    {
        "id": "WKP-F80A7F888D2B156F5ADAEA019209BAF2",
        "title": "差评与重复问题反馈归档",
        "overview": "bad review、repeated issues、negative feedback、product team feedback",
        "path": "/wiki/archive/feedback/repeated-issues",
        "updated_at": "2026-05-27 09:00:00",
        "keywords": ["差评", "重复问题", "反馈", "产品团队", "运营"],
        "faqs": [
            {
                "id": "repeated_issue_archive",
                "q": "重复差评历史上如何反馈？",
                "a": "重复差评历史记录要求汇总问题样本后反馈产品团队，但具体对用户回复仍需结合当前服务规范。",
            },
            {
                "id": "negative_feedback_archive",
                "q": "差评反馈是否一定升级运营？",
                "a": "差评反馈不一定升级运营；只有出现情绪风险且无法安抚时才需要升级运营代表或 SRE Leader。",
            },
        ],
    },
]

# Wiki pages are intentionally embedded in this service. Startup must not read
# judge-only directories or external answer artifacts.
PAGE_BY_ID = {page["id"]: page for page in PAGES}
PAGE_BY_PATH = {page["path"]: page for page in PAGES}
UPDATE_LOGS: list[dict[str, Any]] = []

PUBLIC_FAQ_ACTION_KEYS: dict[str, str] = {
    "login_allowed_cache_files": "PUB-K02",
    "completion_no_response": "PUB-K03",
    "gateway_429": "PUB-K01",
    "gateway_503": "PUB-K05",
    "mcp_connection_failed": "PUB-K03",
    "plugin_log_paths": "PUB-K01",
    "diagnostics_upload_v3": "PUB-K01",
    "rollout_crash_rollback": "PUB-K02",
    "index_building_old": "PUB-K12",
    "proxy_cert_old": "PUB-K02",
    "install_channels": "PUB-K01",
    "install_no_manual_extract": "PUB-K02",
    "upgrade_version_selection": "PUB-K01",
    "uninstall_before_reinstall": "PUB-K02",
    "ticket_required_fields": "PUB-K04",
    "ticket_channel": "PUB-K04",
    "ticket_trace_diag_only": "PUB-K04",
    "legacy_report_api_v1": "PUB-K02",
    "old_clear_cache_all": "PUB-K02",
    "codereviewbot_negative_feedback": "PUB-K13",
    "plugin_market_no_conclusion": "PUB-K13",
    "browser_extension_subscription": "PUB-K13",
    "ide_theme_choice": "PUB-K13",
    "sso_cache_legacy": "PUB-K13",
    "sso_browser_cookie": "PUB-K03",
    "east_proxy_completion_scope": "PUB-K03",
    "proxy_disable_draft": "PUB-K02",
    "mcp_disable_old": "PUB-K13",
    "remote_workspace_logs": "PUB-K03",
    "remote_logs_not_local_only": "PUB-K03",
    "diagnostic_privacy_scope": "PUB-K01",
    "beta_history_wait_release": "PUB-K13",
    "beta_toggle_history": "PUB-K13",
    "remote_index_building": "PUB-K03",
    "remote_index_local_cache": "PUB-K02",
    "proxy_security_pilot": "PUB-K13",
    "proxy_disable_not_allowed": "PUB-K02",
    "manual_extract_old": "PUB-K02",
    "ticket_lightweight_draft": "PUB-K04",
    "ticket_missing_context": "PUB-K04",
    "group_sync_two_hours": "PUB-K13",
    "repeated_issue_archive": "PUB-K11",
    "negative_feedback_archive": "PUB-K06",
}


def shuffled_pages() -> list[dict[str, Any]]:
    pages = list(PAGES)
    random.Random(os.environ.get("WIKI_PAGE_ORDER_SEED", "devpilot-public-pages-v2")).shuffle(pages)
    return pages


def metadata_payload(page: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": page["id"],
        "title": page["title"],
        "overview": page["overview"],
        "updated_at": page["updated_at"],
        "faq_count": len(page["faqs"]),
    }


def page_payload(page: dict[str, Any]) -> dict[str, Any]:
    payload = metadata_payload(page)
    faqs: list[dict[str, Any]] = []
    for faq in page["faqs"]:
        item = dict(faq)
        action_key = PUBLIC_FAQ_ACTION_KEYS.get(str(faq.get("id", "")))
        if action_key is not None:
            item.setdefault("service_action_key", action_key)
        faqs.append(item)
    payload["faqs"] = faqs
    return payload


def normalize_query(value: str) -> str:
    return value.strip().lower()


def split_query_fragments(query: str) -> list[str]:
    fragments: list[str] = []
    current: list[str] = []
    for char in query:
        if char.isalnum() or char in "._-/~%":
            current.append(char)
        else:
            if len(current) >= 2:
                fragments.append("".join(current).lower())
            current = []
    if len(current) >= 2:
        fragments.append("".join(current).lower())
    return fragments


def score_page(query: str, page: dict[str, Any]) -> tuple[int, list[str]]:
    normalized = normalize_query(query)
    page_text = "\n".join([page["title"], page["overview"], page["path"], *page["keywords"]]).lower()
    score = 0
    matched_terms: list[str] = []

    if normalized and normalized in page_text:
        score += 80

    for term in page["keywords"]:
        lowered = term.lower()
        if lowered and lowered in normalized:
            score += 12
            if lowered.isdigit() or lowered.startswith("/") or "." in lowered:
                score += 18
            matched_terms.append(term)

    for fragment in split_query_fragments(normalized):
        if fragment in page_text:
            score += 3

    return score, matched_terms


def search_pages(query: str) -> list[dict[str, Any]]:
    if not normalize_query(query):
        return []

    scored_results: list[tuple[int, str, int, dict[str, Any]]] = []
    for order, page in enumerate(shuffled_pages()):
        score, _matched_terms = score_page(query, page)
        if score > 0:
            item = metadata_payload(page)
            scored_results.append((score, page["updated_at"], -order, item))

    scored_results.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    return [item for _, _, _, item in scored_results[:8]]


class WikiHandler(BaseHTTPRequestHandler):
    server_version = "DevPilotWikiMock/2026.06-linked-faq"

    def log_message(self, format: str, *args: Any) -> None:
        return

    def send_json(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_not_found(self) -> None:
        self.send_json({"error": "not_found"}, 404)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path == "/health":
            self.send_json(
                {
                    "service": "devpilot_wiki_mock",
                    "status": "ok",
                    "update_mode": "proposal_only",
                    "version": SERVICE_VERSION,
                    "page_order": "seeded_shuffle",
                }
            )
            return

        if path == "/api/wiki/pages":
            self.send_json([metadata_payload(page) for page in shuffled_pages()])
            return

        if path.startswith("/api/wiki/pages/"):
            page_id = path.removeprefix("/api/wiki/pages/")
            if "/" in page_id:
                self.send_not_found()
                return
            page = PAGE_BY_ID.get(page_id)
            if page is None:
                self.send_not_found()
                return
            self.send_json(page_payload(page))
            return

        if path == "/api/wiki/search":
            query = parse_qs(parsed.query).get("q", [""])[0]
            self.send_json({"query": query, "results": search_pages(query)})
            return

        if path == "/api/wiki/update_logs":
            self.send_json(UPDATE_LOGS)
            return

        page = PAGE_BY_PATH.get(path)
        if page is not None:
            self.send_json(page_payload(page))
            return

        self.send_not_found()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        prefix = "/api/wiki/pages/"
        suffix = "/update_proposal"

        if not (path.startswith(prefix) and path.endswith(suffix)):
            self.send_not_found()
            return

        page_id = path[len(prefix) : -len(suffix)]
        if page_id not in PAGE_BY_ID:
            self.send_not_found()
            return

        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self.send_json({"error": "invalid_json"}, 400)
            return

        content = str(payload.get("content", "")).strip()
        updated_at = str(payload.get("updated_at", "")).strip()
        log = {
            "page_id": page_id,
            "content": content,
            "updated_at": updated_at,
            "mode": "proposal_only",
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        UPDATE_LOGS.append(log)
        self.send_json(
            {
                "accepted": True,
                "content": content,
                "message": "更新建议已记录，原始 Wiki 页面不会被修改",
                "mode": "proposal_only",
                "page_id": page_id,
                "updated_at": updated_at,
            }
        )


class ExclusiveThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


def is_address_in_use(exc: OSError) -> bool:
    return getattr(exc, "winerror", None) == 10048 or getattr(exc, "errno", None) in {48, 98, 10048}


def existing_wiki_is_healthy(host: str, port: int) -> bool:
    url = f"http://{host}:{port}/health"
    try:
        with urlopen(url, timeout=2) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (OSError, URLError, json.JSONDecodeError):
        return False
    return payload.get("service") == "devpilot_wiki_mock" and payload.get("status") == "ok"


_WINDOWS_CONSOLE_HANDLER = None


def install_windows_console_exit_handler(httpd: ThreadingHTTPServer) -> None:
    """Ensure closing a double-clicked console window terminates the service."""
    if os.name != "nt":
        return
    try:
        import ctypes
    except Exception:
        return

    ctrl_close_event = 2
    ctrl_logoff_event = 5
    ctrl_shutdown_event = 6
    handled_events = {ctrl_close_event, ctrl_logoff_event, ctrl_shutdown_event}

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)
    def handler(ctrl_type: int) -> bool:
        if ctrl_type not in handled_events:
            return False
        try:
            httpd.shutdown()
            httpd.server_close()
        finally:
            os._exit(0)

    global _WINDOWS_CONSOLE_HANDLER
    _WINDOWS_CONSOLE_HANDLER = handler
    ctypes.windll.kernel32.SetConsoleCtrlHandler(handler, True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("port_arg", nargs="?", type=int, help="Optional service port. Equivalent to --port.")
    parser.add_argument("--host", default=os.environ.get("WIKI_HOST", HOST))
    parser.add_argument("-host", dest="host_alias", default=None)
    parser.add_argument("-port", "--port", default=PORT, type=int)
    args = parser.parse_args()
    if args.host_alias is not None:
        args.host = args.host_alias
    if args.port_arg is not None:
        args.port = args.port_arg

    try:
        httpd = ExclusiveThreadingHTTPServer((args.host, args.port), WikiHandler)
    except OSError as exc:
        if is_address_in_use(exc):
            if existing_wiki_is_healthy(args.host, args.port):
                print(f"DevPilot Wiki mock service already running on http://{args.host}:{args.port}", flush=True)
                return
            raise SystemExit(
                f"failed to start wiki service: http://{args.host}:{args.port} is already in use. "
                "Close the process using this port or start with -port <other_port>."
            ) from None
        raise
    print(f"DevPilot Wiki mock service listening on http://{args.host}:{args.port}", flush=True)
    print("Close this window or press Ctrl+C to stop the service.", flush=True)
    install_windows_console_exit_handler(httpd)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
