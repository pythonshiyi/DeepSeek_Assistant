# -*- coding: utf-8 -*-
"""Dialog package: re-exports the original dialogs module public API."""

from .common import FONT_FAMILY, MONO_FAMILY
from .about_help import (
    show_about,
    show_balance,
    show_help,
    show_welcome,
    show_plugin_guide,
)
from .data_stats import (
    show_stats,
    show_dependencies,
    show_failures,
    show_tasklog,
    show_checkpoint,
    show_recent_outputs,
    show_stars,
    show_feature_suggestions,
    show_evolution_audit,
)
from .workspace import (
    choose_working_dir,
    show_cleanup,
    show_workspace_tree,
)
from .session import (
    show_history_sessions,
    show_command_palette,
    show_context_details,
    show_session_timeline,
)
from .productivity import (
    show_batch_task,
    show_fim_dialog,
    show_variants,
    show_evolutions,
)

__all__ = [
    "FONT_FAMILY",
    "MONO_FAMILY",
    "show_about",
    "show_balance",
    "show_help",
    "show_welcome",
    "show_plugin_guide",
    "show_stats",
    "show_dependencies",
    "show_failures",
    "show_tasklog",
    "show_checkpoint",
    "show_recent_outputs",
    "show_stars",
    "show_feature_suggestions",
    "show_evolution_audit",
    "choose_working_dir",
    "show_cleanup",
    "show_workspace_tree",
    "show_history_sessions",
    "show_command_palette",
    "show_context_details",
    "show_session_timeline",
    "show_batch_task",
    "show_fim_dialog",
    "show_variants",
    "show_evolutions",
]
