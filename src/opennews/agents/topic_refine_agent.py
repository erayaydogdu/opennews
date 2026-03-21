"""Topic Refine Agent — uses LLM to refine clustering results for topic refinement.

Solves the mis-aggregation problem of pure embedding similarity clustering:
sends candidate group news to LLM to determine which truly belong to the same topic,
and splits unrelated news into independent sub-groups.
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass

from opennews.llm.client import LLMClient, LLMConfig
from opennews.topic.online_topic_model import TopicAssignment

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RefinedGroup:
    """A topic sub-group after LLM refinement."""
    label_zh: str
    label_en: str
    member_indices: list[int]  # Indices in the original candidate group

    @property
    def label_dict(self) -> dict[str, str]:
        return {"zh": self.label_zh, "en": self.label_en}


class TopicRefineAgent:
    """Calls LLM to perform fine-grained splitting of clustering candidate groups."""

    # Max titles per LLM call; batched if exceeded
    _MAX_BATCH_SIZE = 20

    def __init__(self, config: LLMConfig | None = None):
        self.config = config or LLMConfig.load()
        self._client = LLMClient(self.config)

    def refine_topics(
        self,
        docs: list[str],
        assignments: list[TopicAssignment],
        labels: dict[int, dict[str, str]],
    ) -> tuple[list[TopicAssignment], dict[int, dict[str, str]]]:
        """Refine clustering results with LLM.

        Args:
            docs: News document list (title\\ncontent)
            assignments: Original clustering assignments
            labels: Original topic_id → {"zh": "...", "en": "..."} mapping

        Returns:
            (refined_assignments, refined_labels) Refined assignments and bilingual labels
        """
        if not self.config.topic_refine_enabled:
            logger.info("topic refine disabled, skipping")
            return assignments, labels

        if not self.config.api_key:
            logger.warning("LLM api_key not configured, skipping topic refine")
            return assignments, labels

        # Group by topic_id (only process aggregated topics >=0; solo items don't need refining)
        groups: dict[int, list[int]] = {}
        for i, a in enumerate(assignments):
            if a.topic_id >= 0:
                groups.setdefault(a.topic_id, []).append(i)

        if not groups:
            return assignments, labels

        # Build new assignment results
        new_assignments = list(assignments)  # Shallow copy
        new_labels = dict(labels)
        next_topic_id = max((a.topic_id for a in assignments), default=-1) + 1
        next_solo_id = min((a.topic_id for a in assignments), default=0)
        if next_solo_id >= 0:
            next_solo_id = -1

        for tid, member_indices in groups.items():
            if len(member_indices) <= 1:
                continue  # Single item, no refinement needed

            titles = [docs[i].split("\n")[0] for i in member_indices]

            # Batch LLM calls for oversized groups, each batch refined independently
            if len(titles) > self._MAX_BATCH_SIZE:
                logger.info(
                    "topic %d has %d items, splitting into batches of %d for LLM refine",
                    tid, len(titles), self._MAX_BATCH_SIZE,
                )
                all_refined: list[RefinedGroup] = []
                for batch_start in range(0, len(titles), self._MAX_BATCH_SIZE):
                    batch_titles = titles[batch_start:batch_start + self._MAX_BATCH_SIZE]
                    batch_refined = self._call_llm_with_retry(tid, batch_titles)
                    if batch_refined is None:
                        # This batch failed, keep original indices
                        for local_i in range(len(batch_titles)):
                            all_refined.append(RefinedGroup(
                                label_zh="Uncategorized", label_en="Uncategorized",
                                member_indices=[batch_start + local_i],
                            ))
                    else:
                        # Offset local batch indices to group-global indices
                        for rg in batch_refined:
                            shifted = [batch_start + idx for idx in rg.member_indices]
                            all_refined.append(RefinedGroup(
                                label_zh=rg.label_zh, label_en=rg.label_en,
                                member_indices=shifted,
                            ))
                refined = all_refined
            else:
                refined = self._call_llm_with_retry(tid, titles)

            if refined is None:
                continue

            if not refined or (len(refined) == 1 and
                    sorted(refined[0].member_indices) == list(range(len(titles)))):
                # LLM considers all items as same topic, keep unchanged
                continue

            logger.info("topic %d split into %d sub-groups by LLM", tid, len(refined))

            # First sub-group inherits original topic_id
            first = True
            for rg in refined:
                if not rg.member_indices:
                    continue

                if first:
                    # Inherit original topic_id, update label
                    use_tid = tid
                    new_labels[use_tid] = rg.label_dict
                    first = False
                elif len(rg.member_indices) == 1:
                    # Single news item → solo
                    use_tid = next_solo_id
                    new_labels[use_tid] = rg.label_dict
                    next_solo_id -= 1
                else:
                    # New aggregated topic
                    use_tid = next_topic_id
                    new_labels[use_tid] = rg.label_dict
                    next_topic_id += 1

                for local_idx in rg.member_indices:
                    if local_idx < len(member_indices):
                        global_idx = member_indices[local_idx]
                        new_assignments[global_idx] = TopicAssignment(
                            topic_id=use_tid,
                            probability=assignments[global_idx].probability,
                        )

        clustered = sum(1 for a in new_assignments if a.topic_id >= 0)
        logger.info("after LLM refine: %d clustered, %d solo",
                     clustered, len(new_assignments) - clustered)

        # ── Batch-translate topics missing bilingual labels ──────────────
        # When clustering or LLM fails, label zh/en are identical (both original title); need supplementary translation
        new_labels = self._translate_missing_labels(new_labels)

        # ── Fallback: when LLM translation also fails, use rules to distinguish Chinese/English ──────
        new_labels = self._fallback_bilingual(new_labels)

        return new_assignments, new_labels

    # ── Retry untranslated labels ────────────────────────────────────

    def retry_failed_labels(
        self, failed: list[tuple[int, dict[str, str]]],
    ) -> list[tuple[int, dict[str, str]]]:
        """Retry LLM translation for labels with [EN]/[ZH] prefix.

        Args:
            failed: [(record_id, {"zh": "...", "en": "..."}), ...]

        Returns:
            Successfully translated [(record_id, {"zh": "...", "en": "..."}), ...]
        """
        if not self.config.api_key or not failed:
            return []

        # Restore original text: strip [EN]/[ZH] prefix, set zh==en to trigger translation logic
        restore_map: dict[int, tuple[int, str]] = {}  # fake_tid → (record_id, original_text)
        labels: dict[int, dict[str, str]] = {}
        for i, (record_id, lbl) in enumerate(failed):
            zh, en = lbl.get("zh", ""), lbl.get("en", "")
            if zh.startswith("[EN] "):
                original = en  # en is the original English
            elif en.startswith("[ZH] "):
                original = zh  # zh is the original Chinese
            else:
                continue
            fake_tid = -(10000 + i)
            restore_map[fake_tid] = (record_id, original)
            labels[fake_tid] = {"zh": original, "en": original}

        if not labels:
            return []

        logger.info("retrying translation for %d failed topic labels", len(labels))
        translated = self._translate_missing_labels(labels)

        results: list[tuple[int, dict[str, str]]] = []
        for fake_tid, new_lbl in translated.items():
            zh, en = new_lbl.get("zh", ""), new_lbl.get("en", "")
            # Only keep truly successful translations (zh != en and no prefix)
            if zh and en and zh != en and not zh.startswith("[EN]") and not en.startswith("[ZH]"):
                record_id, _ = restore_map[fake_tid]
                results.append((record_id, new_lbl))

        logger.info("successfully re-translated %d/%d labels", len(results), len(labels))
        return results

    # ── Local rule fallback ──────────────────────────────────────

    @staticmethod
    def _is_mostly_chinese(text: str) -> bool:
        """Determine whether text is predominantly Chinese characters."""
        if not text:
            return False
        cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
        return cjk / max(len(text.replace(" ", "")), 1) > 0.3

    @staticmethod
    def _fallback_bilingual(
        labels: dict[int, dict[str, str]],
    ) -> dict[int, dict[str, str]]:
        """Local fallback when LLM translation completely fails: set the other language to a prefixed placeholder based on text language.

        If the original title is Chinese, en is set to "[ZH] original_title";
        if the original title is English, zh is set to "[EN] original_title".
        This way the frontend can at least distinguish which is the original language and which is a placeholder.
        """
        result = dict(labels)
        for tid, lbl in result.items():
            zh, en = lbl.get("zh", ""), lbl.get("en", "")
            if zh != en or not zh:
                continue  # Already different, skip
            if TopicRefineAgent._is_mostly_chinese(zh):
                result[tid] = {"zh": zh, "en": f"[ZH] {zh}"}
            else:
                result[tid] = {"zh": f"[EN] {en}", "en": en}
        return result

    # ── Batch translation ──────────────────────────────────────────

    _TRANSLATE_BATCH_SIZE = 40  # Max items per translation request

    def _translate_missing_labels(
        self, labels: dict[int, dict[str, str]],
    ) -> dict[int, dict[str, str]]:
        """Batch-call LLM to generate missing language labels for entries where zh == en."""
        if not self.config.api_key:
            return labels

        # Collect topic_ids and titles that need translation
        to_translate: list[tuple[int, str]] = []
        for tid, lbl in labels.items():
            if lbl.get("zh") == lbl.get("en") and lbl.get("zh"):
                to_translate.append((tid, lbl["zh"]))

        if not to_translate:
            return labels

        logger.info("translating %d topic labels to bilingual", len(to_translate))
        result = dict(labels)

        for batch_start in range(0, len(to_translate), self._TRANSLATE_BATCH_SIZE):
            batch = to_translate[batch_start:batch_start + self._TRANSLATE_BATCH_SIZE]
            translated = self._call_translate_batch(batch)
            if translated:
                for (tid, _orig), pair in zip(batch, translated):
                    if pair is not None:
                        result[tid] = {"zh": pair[0], "en": pair[1]}

        return result

    def _call_translate_batch(
        self, items: list[tuple[int, str]],
    ) -> list[tuple[str, str]] | None:
        """Batch-translate titles, return [(zh, en), ...] or None."""
        numbered = "\n".join(f"[{i}] {title}" for i, (_, title) in enumerate(items))

        system = (
            "You are a multilingual news headline translation expert. "
            "You need to provide concise Chinese and English topic labels for each news headline. "
            "If the original title is Chinese, generate the corresponding English label; if the original title is English, generate the corresponding Chinese label. "
            "Labels should summarize the core news content, 10-20 characters (Chinese) or 5-10 words (English)."
        )
        user = (
            f"Generate bilingual topic labels for the following news headlines:\n\n{numbered}\n\n"
            'Output strict JSON array:\n'
            '[{"zh": "Chinese label", "en": "English label"}]\n\n'
            "Requirements:\n"
            "1. Array length must match the number of input entries, in corresponding order\n"
            "2. Output only the JSON array"
        )

        try:
            raw = self._client.chat(system, user)
        except Exception as e:
            logger.warning("translate batch failed: %s", e)
            return None

        return self._parse_translate_response(raw, len(items))

    @staticmethod
    def _parse_translate_response(raw: str, expected: int) -> list[tuple[str, str]] | None:
        """Parse the JSON array returned by the translation LLM."""
        json_match = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
        text = json_match.group(1).strip() if json_match else raw.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            bracket_match = re.search(r"\[.*\]", text, re.DOTALL)
            if bracket_match:
                try:
                    data = json.loads(bracket_match.group())
                except json.JSONDecodeError:
                    logger.warning("failed to parse translate response: %s", text[:200])
                    return None
            else:
                logger.warning("no JSON array in translate response: %s", text[:200])
                return None

        if not isinstance(data, list):
            return None

        result = []
        for item in data[:expected]:
            if isinstance(item, dict):
                zh = item.get("zh", "")
                en = item.get("en", "")
                if zh and en:
                    result.append((zh, en))
                else:
                    result.append(None)
            else:
                result.append(None)

        # Pad insufficient entries
        while len(result) < expected:
            result.append(None)

        return result

    def _call_llm_with_retry(self, tid: int, titles: list[str]) -> list[RefinedGroup] | None:
        """LLM refinement call with retries, returns None on failure."""
        max_retries = max(0, self.config.topic_refine_max_retries)
        last_err: Exception | None = None

        for attempt in range(1 + max_retries):
            try:
                return self._call_llm_refine(titles)
            except Exception as e:
                last_err = e
                if attempt < max_retries:
                    wait = 2 ** attempt
                    logger.warning(
                        "LLM refine failed for topic %d (attempt %d/%d): %s — retrying in %ds",
                        tid, attempt + 1, 1 + max_retries, e, wait,
                    )
                    time.sleep(wait)

        logger.warning(
            "LLM refine failed for topic %d after %d attempts (%d items), "
            "keeping original clustering. "
            "Check LLM API connectivity and config/llm.yaml settings. "
            "Last error: %s",
            tid, 1 + max_retries, len(titles), last_err,
        )
        return None

    def _call_llm_refine(self, titles: list[str]) -> list[RefinedGroup]:
        """Call LLM to perform topic splitting on a group of news headlines."""
        news_list = "\n".join(f"[{i}] {t}" for i, t in enumerate(titles))

        system = self.config.topic_refine_system_prompt
        user_template = self.config.topic_refine_user_prompt_template

        if not system or not user_template:
            # Use built-in default prompt
            system = (
                "You are a news topic clustering analyst. "
                "You excel at identifying which news headlines report on the same event or topic, and which are only superficially similar but actually unrelated. "
                "The criteria are the core event, subject, and causal relationship discussed in the news, not shared broad keywords or domain."
            )
            user_template = (
                "The following news items were preliminarily grouped as the same topic. Please re-examine and regroup:\n\n{news_list}\n\n"
                "Group news that truly discusses the same event/topic together, and split out unrelated ones.\n\n"
                'Output strict JSON:\n'
                '{{"groups": [{{"label_zh": "Summarized Chinese topic label, 10-20 chars", "label_en": "Concise English topic label, 5-10 words", "indices": [0, 2]}}]}}\n\n'
                "Requirements:\n"
                "1. indices are news sequence numbers (starting from 0), each news item can only appear once\n"
                "2. News unrelated to the rest of the group should be split into its own group\n"
                "3. label_zh should accurately summarize the group's common topic (Chinese), label_en is the corresponding English topic label\n"
                "4. Output only JSON"
            )

        user = user_template.replace("{news_list}", news_list)
        raw = self._client.chat(system, user)
        return self._parse_response(raw, len(titles))

    @staticmethod
    def _parse_response(raw: str, n_titles: int) -> list[RefinedGroup]:
        """Parse JSON returned by LLM, with error tolerance."""
        # Extract JSON block (compatible with markdown code block)
        json_match = re.search(r"```(?:json)?\s*(.*?)```", raw, re.DOTALL)
        text = json_match.group(1).strip() if json_match else raw.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Try to fix common issues: extract first { ... }
            brace_match = re.search(r"\{.*\}", text, re.DOTALL)
            if brace_match:
                try:
                    data = json.loads(brace_match.group())
                except json.JSONDecodeError:
                    logger.warning("failed to parse LLM response as JSON: %s", text[:200])
                    return []
            else:
                logger.warning("no JSON found in LLM response: %s", text[:200])
                return []

        groups_raw = data.get("groups", [])
        if not isinstance(groups_raw, list):
            return []

        seen = set()
        result = []
        for g in groups_raw:
            label_zh = g.get("label_zh") or g.get("label", "Unknown Topic")
            label_en = g.get("label_en", label_zh)
            indices = g.get("indices", [])
            # Filter invalid indices and duplicates
            valid = [i for i in indices if isinstance(i, int) and 0 <= i < n_titles and i not in seen]
            seen.update(valid)
            if valid:
                result.append(RefinedGroup(label_zh=label_zh, label_en=label_en, member_indices=valid))

        # Fill in missing news items (LLM may have omitted some)
        missing = [i for i in range(n_titles) if i not in seen]
        for i in missing:
            result.append(RefinedGroup(label_zh="Uncategorized", label_en="Uncategorized", member_indices=[i]))

        return result
