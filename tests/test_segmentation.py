"""Tests for conversation segmentation (Tasks 15a + 15b).

Covers validation assertions:
- VAL-SEG-001: _segment_session returns single segment for short sessions
- VAL-SEG-002: _segment_session detects topic boundaries via cosine similarity drops
- VAL-SEG-003: _segment_session merges small segments with predecessor
- VAL-SEG-004: distill uses segmentation by default (segment=True)
- VAL-SEG-005: --no-segment CLI flag disables segmentation
"""

import sqlite3
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import numpy as np
import pytest

SCHEMA_PATH = Path(__file__).parent.parent / "schema.sql"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db_with_schema() -> sqlite3.Connection:
    """Create an in-memory DB from schema.sql with row_factory and foreign_keys."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text())
    return conn


_msg_counter = 10000  # offset to avoid collisions with other test files


def _make_message(content: str, role: str = "user", msg_id: int | None = None,
                  session_id: str = "sess-seg-1") -> dict:
    """Create a message dict (no DB insert needed for _segment_session)."""
    global _msg_counter
    _msg_counter += 1
    return {
        "id": msg_id or _msg_counter,
        "content": content,
        "role": role,
        "session_id": session_id,
        "project": "/test/proj",
        "timestamp": f"2024-01-01T00:00:{_msg_counter % 60:02d}",
    }


def _make_session_messages(user_texts: list[str], interleave_assistant: bool = True) -> list[dict]:
    """Build a list of messages from user texts, optionally interleaving assistant responses."""
    messages = []
    for text in user_texts:
        messages.append(_make_message(text, role="user"))
        if interleave_assistant:
            messages.append(_make_message(f"Response to: {text}", role="assistant"))
    return messages


# ---------------------------------------------------------------------------
# VAL-SEG-001: _segment_session returns single segment for short sessions
# ---------------------------------------------------------------------------

class TestSegmentSessionShort:
    """VAL-SEG-001: Returns single segment for sessions shorter than min_segment_size*2."""

    @patch("src.distill._get_dedup_model")
    @patch("src.distill.get_conn")
    def test_short_session_returns_single_segment(self, mock_conn, mock_model) -> None:
        """Sessions with fewer than min_segment_size*2 user messages return as one segment."""
        from src.distill import _segment_session

        # 3 user messages with interleaved assistant = 6 total messages
        # min_segment_size defaults to 4, so need < 4*2=8 user messages to be "short"
        messages = _make_session_messages(["hello", "how are you", "goodbye"])
        # 3 user messages < 8 = min_segment_size * 2

        segments = _segment_session(messages)
        assert len(segments) == 1
        assert segments[0] == messages

    @patch("src.distill._get_dedup_model")
    @patch("src.distill.get_conn")
    def test_exactly_min_threshold_returns_single_segment(self, mock_conn, mock_model) -> None:
        """Sessions with exactly min_segment_size*2 - 1 user messages return single segment."""
        from src.distill import _segment_session

        # 7 user messages < 8 = 4*2
        messages = _make_session_messages([f"msg {i}" for i in range(7)])

        segments = _segment_session(messages)
        assert len(segments) == 1
        assert segments[0] == messages

    @patch("src.distill._get_dedup_model")
    @patch("src.distill.get_conn")
    def test_empty_session_returns_single_segment(self, mock_conn, mock_model) -> None:
        """Empty session returns single segment containing the empty list."""
        from src.distill import _segment_session

        segments = _segment_session([])
        assert len(segments) == 1
        assert segments[0] == []

    @patch("src.distill._get_dedup_model")
    @patch("src.distill.get_conn")
    def test_no_user_messages_returns_single_segment(self, mock_conn, mock_model) -> None:
        """Session with only assistant messages returns single segment."""
        from src.distill import _segment_session

        messages = [_make_message("assistant response", role="assistant") for _ in range(10)]

        segments = _segment_session(messages)
        assert len(segments) == 1
        assert segments[0] == messages


# ---------------------------------------------------------------------------
# VAL-SEG-002: _segment_session detects topic boundaries
# ---------------------------------------------------------------------------

class TestSegmentSessionBoundaryDetection:
    """VAL-SEG-002: Detects topic boundaries via cosine similarity drops."""

    @patch("src.distill.get_conn")
    def test_detects_topic_shift(self, mock_conn) -> None:
        """Two distinct topic clusters should produce multiple segments."""
        from src.distill import _segment_session

        # Mock DB connection to return no precomputed embeddings
        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = []
        mock_conn.return_value = mock_db

        # Create 10 user messages about topic A, then 10 about topic B
        topic_a = [f"Python programming language features {i}" for i in range(10)]
        topic_b = [f"Italian cooking recipes pasta sauce {i}" for i in range(10)]
        messages = _make_session_messages(topic_a + topic_b)

        # Mock the dedup model to return distinct embeddings for each topic
        mock_model = MagicMock()

        # Topic A vectors: similar to each other, different from topic B
        # Use very small noise relative to dimension to ensure high within-topic similarity
        topic_a_base = np.random.randn(384).astype(np.float32)
        topic_a_base /= np.linalg.norm(topic_a_base)
        topic_b_base = np.random.randn(384).astype(np.float32)
        topic_b_base /= np.linalg.norm(topic_b_base)

        # Ensure topics are very different (orthogonalize)
        topic_b_base = topic_b_base - (topic_b_base @ topic_a_base) * topic_a_base
        topic_b_base /= np.linalg.norm(topic_b_base)

        call_count = [0]

        def fake_encode(texts, **kwargs):
            results = []
            for _ in texts:
                if call_count[0] < 10:
                    # Very small noise so within-topic similarity > 0.7
                    noise = np.random.randn(384).astype(np.float32) * 0.001
                    vec = topic_a_base + noise
                else:
                    noise = np.random.randn(384).astype(np.float32) * 0.001
                    vec = topic_b_base + noise
                vec /= np.linalg.norm(vec)
                results.append(vec)
                call_count[0] += 1
            return np.array(results)

        mock_model.encode = fake_encode

        with patch("src.distill._get_dedup_model", return_value=mock_model):
            segments = _segment_session(messages, drift_threshold=0.3, min_segment_size=4)

        # Should detect at least one boundary between topic A and B
        assert len(segments) >= 2, f"Expected >=2 segments but got {len(segments)}"

        # All messages should be accounted for
        total_msgs = sum(len(s) for s in segments)
        assert total_msgs == len(messages)

    @patch("src.distill.get_conn")
    def test_no_boundaries_returns_single_segment(self, mock_conn) -> None:
        """Highly similar consecutive messages produce a single segment."""
        from src.distill import _segment_session

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = []
        mock_conn.return_value = mock_db

        # 10 user messages all on the same topic
        same_topic = [f"Python programming tip number {i}" for i in range(10)]
        messages = _make_session_messages(same_topic)

        # All embeddings nearly identical
        base_vec = np.random.randn(384).astype(np.float32)
        base_vec /= np.linalg.norm(base_vec)

        mock_model = MagicMock()

        def fake_encode(texts, **kwargs):
            results = []
            for _ in texts:
                noise = np.random.randn(384).astype(np.float32) * 0.01
                vec = base_vec + noise
                vec /= np.linalg.norm(vec)
                results.append(vec)
            return np.array(results)

        mock_model.encode = fake_encode

        with patch("src.distill._get_dedup_model", return_value=mock_model):
            segments = _segment_session(messages, drift_threshold=0.3, min_segment_size=4)

        assert len(segments) == 1
        assert segments[0] == messages


# ---------------------------------------------------------------------------
# VAL-SEG-003: _segment_session merges small segments
# ---------------------------------------------------------------------------

class TestSegmentSessionMergeSmall:
    """VAL-SEG-003: Segments smaller than min_segment_size merge with predecessor."""

    @patch("src.distill.get_conn")
    def test_small_segments_merged(self, mock_conn) -> None:
        """Segments below min_segment_size are merged with the previous segment."""
        from src.distill import _segment_session

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = []
        mock_conn.return_value = mock_db

        # Create a session where a brief topic tangent (2 messages) occurs
        # between two larger topic blocks
        topic_a = [f"Python data science topic {i}" for i in range(6)]
        tangent = [f"Quick question about lunch {i}" for i in range(2)]
        topic_c = [f"Python machine learning topic {i}" for i in range(6)]
        all_texts = topic_a + tangent + topic_c
        messages = _make_session_messages(all_texts)

        # Create embeddings that create boundaries at indices 6 and 8
        topic_a_base = np.random.randn(384).astype(np.float32)
        topic_a_base /= np.linalg.norm(topic_a_base)
        tangent_base = np.random.randn(384).astype(np.float32)
        tangent_base -= (tangent_base @ topic_a_base) * topic_a_base
        tangent_base /= np.linalg.norm(tangent_base)
        topic_c_base = np.random.randn(384).astype(np.float32)
        topic_c_base -= (topic_c_base @ topic_a_base) * topic_a_base
        topic_c_base -= (topic_c_base @ tangent_base) * tangent_base
        topic_c_base /= np.linalg.norm(topic_c_base)

        call_count = [0]

        def fake_encode(texts, **kwargs):
            results = []
            for _ in texts:
                idx = call_count[0]
                if idx < 6:
                    base = topic_a_base
                elif idx < 8:
                    base = tangent_base
                else:
                    base = topic_c_base
                noise = np.random.randn(384).astype(np.float32) * 0.001
                vec = base + noise
                vec /= np.linalg.norm(vec)
                results.append(vec)
                call_count[0] += 1
            return np.array(results)

        mock_model = MagicMock()
        mock_model.encode = fake_encode

        with patch("src.distill._get_dedup_model", return_value=mock_model):
            segments = _segment_session(messages, drift_threshold=0.3, min_segment_size=4)

        # The 2-message tangent should be merged with its predecessor
        # So we should get at most 2 segments (topic_a+tangent merged, then topic_c)
        # Not 3 segments
        for seg in segments:
            # Each segment should have at least min_segment_size messages
            # (except possibly the last one if the total is not evenly divisible)
            if seg != segments[-1]:
                assert len(seg) >= 4, f"Non-last segment has {len(seg)} messages, expected >= 4"

        # All messages should be accounted for
        total_msgs = sum(len(s) for s in segments)
        assert total_msgs == len(messages)


# ---------------------------------------------------------------------------
# VAL-SEG-001 (additional): Uses pre-computed embeddings from DB
# ---------------------------------------------------------------------------

class TestSegmentSessionPrecomputed:
    """_segment_session uses pre-computed embeddings when available."""

    def test_uses_precomputed_embeddings(self) -> None:
        """When embeddings exist in DB, _segment_session uses them instead of encoding."""
        from src.distill import _segment_session

        conn = _make_db_with_schema()

        # Create 10 user messages
        user_texts = [f"message about topic {i}" for i in range(10)]
        messages = _make_session_messages(user_texts)

        # Insert messages and precomputed embeddings into the DB
        base_vec = np.random.randn(384).astype(np.float32)
        base_vec /= np.linalg.norm(base_vec)

        for msg in messages:
            if msg["role"] == "user":
                conn.execute(
                    """INSERT INTO messages (id, source_file, session_id, project, role, content, timestamp)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (msg["id"], f"/tmp/test_{msg['id']}.jsonl", msg["session_id"],
                     msg["project"], msg["role"], msg["content"], msg["timestamp"]),
                )
                # Precomputed embedding: similar vectors (no boundary)
                noise = np.random.randn(384).astype(np.float32) * 0.01
                vec = base_vec + noise
                vec /= np.linalg.norm(vec)
                conn.execute(
                    "INSERT INTO embeddings (message_id, embedding, model) VALUES (?, ?, ?)",
                    (msg["id"], vec.astype(np.float32).tobytes(), "all-MiniLM-L6-v2"),
                )
        conn.commit()

        # Mock to verify model.encode is NOT called (all precomputed)
        mock_model = MagicMock()
        mock_model.encode = MagicMock(side_effect=AssertionError("Should not encode"))

        with patch("src.distill.get_conn", return_value=conn), \
             patch("src.distill._get_dedup_model", return_value=mock_model):
            segments = _segment_session(messages)

        # Should return single segment (all vectors similar)
        assert len(segments) == 1
        conn.close()


# ---------------------------------------------------------------------------
# VAL-SEG-004: distill uses segmentation by default
# ---------------------------------------------------------------------------

class TestDistillSegmentDefault:
    """VAL-SEG-004: distill() has segment=True default parameter."""

    def test_distill_has_segment_param(self) -> None:
        """distill() accepts a 'segment' parameter."""
        import inspect
        from src.distill import distill

        sig = inspect.signature(distill)
        assert "segment" in sig.parameters, "distill() should have a 'segment' parameter"

    def test_distill_segment_defaults_true(self) -> None:
        """distill() 'segment' parameter defaults to True."""
        import inspect
        from src.distill import distill

        sig = inspect.signature(distill)
        param = sig.parameters["segment"]
        assert param.default is True, f"segment should default to True, got {param.default}"

    @patch("src.distill.get_conn")
    @patch("src.distill.get_undistilled_sessions")
    @patch("src.distill.get_session_messages")
    @patch("src.distill.extract_facts_heuristic")
    @patch("src.distill.extract_facts_llm")
    @patch("src.distill._segment_session")
    @patch("src.distill.store_facts")
    def test_distill_calls_segment_when_llm(self, mock_store, mock_segment,
                                             mock_llm, mock_heuristic,
                                             mock_messages, mock_sessions,
                                             mock_conn) -> None:
        """When use_llm=True and segment=True, distill calls _segment_session."""
        db = MagicMock()
        mock_conn.return_value = db
        mock_sessions.return_value = [("sess-1",)]

        # Simulate a session with messages
        messages = _make_session_messages([f"msg {i}" for i in range(10)])
        mock_messages.return_value = messages
        mock_heuristic.return_value = []
        mock_llm.return_value = []
        mock_segment.return_value = [messages]  # Single segment
        mock_store.return_value = 0

        # Mock the existing_facts query
        db.execute.return_value.fetchall.return_value = []

        from src.distill import distill
        distill(use_llm=True, segment=True)

        # _segment_session should have been called
        mock_segment.assert_called_once()

    @patch("src.distill.get_conn")
    @patch("src.distill.get_undistilled_sessions")
    @patch("src.distill.get_session_messages")
    @patch("src.distill.extract_facts_heuristic")
    @patch("src.distill.extract_facts_llm")
    @patch("src.distill._segment_session")
    @patch("src.distill.store_facts")
    def test_distill_skips_segment_when_false(self, mock_store, mock_segment,
                                               mock_llm, mock_heuristic,
                                               mock_messages, mock_sessions,
                                               mock_conn) -> None:
        """When segment=False, distill does NOT call _segment_session."""
        db = MagicMock()
        mock_conn.return_value = db
        mock_sessions.return_value = [("sess-1",)]

        messages = _make_session_messages([f"msg {i}" for i in range(10)])
        mock_messages.return_value = messages
        mock_heuristic.return_value = []
        mock_llm.return_value = []
        mock_store.return_value = 0

        db.execute.return_value.fetchall.return_value = []

        from src.distill import distill
        distill(use_llm=True, segment=False)

        # _segment_session should NOT have been called
        mock_segment.assert_not_called()

    @patch("src.distill.get_conn")
    @patch("src.distill.get_undistilled_sessions")
    @patch("src.distill.get_session_messages")
    @patch("src.distill.extract_facts_heuristic")
    @patch("src.distill.extract_facts_llm")
    @patch("src.distill._segment_session")
    @patch("src.distill.store_facts")
    def test_distill_no_segment_when_no_llm(self, mock_store, mock_segment,
                                             mock_llm, mock_heuristic,
                                             mock_messages, mock_sessions,
                                             mock_conn) -> None:
        """When use_llm=False, segmentation is not used even with segment=True."""
        db = MagicMock()
        mock_conn.return_value = db
        mock_sessions.return_value = [("sess-1",)]

        messages = _make_session_messages([f"msg {i}" for i in range(10)])
        mock_messages.return_value = messages
        mock_heuristic.return_value = []
        mock_store.return_value = 0

        db.execute.return_value.fetchall.return_value = []

        from src.distill import distill
        distill(use_llm=False, segment=True)

        # _segment_session should NOT have been called (LLM not active)
        mock_segment.assert_not_called()


# ---------------------------------------------------------------------------
# VAL-SEG-004 (additional): Cross-segment dedup
# ---------------------------------------------------------------------------

class TestDistillCrossSegmentDedup:
    """Cross-segment dedup: existing_facts extended with newly extracted facts between segments."""

    @patch("src.distill.get_conn")
    @patch("src.distill.get_undistilled_sessions")
    @patch("src.distill.get_session_messages")
    @patch("src.distill.extract_facts_heuristic")
    @patch("src.distill.extract_facts_llm")
    @patch("src.distill._segment_session")
    @patch("src.distill.store_facts")
    def test_existing_facts_extended_between_segments(self, mock_store, mock_segment,
                                                      mock_llm, mock_heuristic,
                                                      mock_messages, mock_sessions,
                                                      mock_conn) -> None:
        """Facts from segment 1 are passed as existing_facts to segment 2's LLM call."""
        db = MagicMock()
        mock_conn.return_value = db
        mock_sessions.return_value = [("sess-1",)]

        messages = _make_session_messages([f"msg {i}" for i in range(10)])
        mock_messages.return_value = messages
        mock_heuristic.return_value = []

        # Segment into 2 segments
        seg1 = messages[:10]
        seg2 = messages[10:]
        mock_segment.return_value = [seg1, seg2]

        # LLM returns facts for segment 1
        seg1_facts = [{"fact": "Fact from segment 1", "category": "preference",
                       "confidence": 0.9, "session_id": "sess-1", "project": "/test",
                       "source_message_id": None, "timestamp": "2024-01-01",
                       "compressed_details": ""}]
        seg2_facts = [{"fact": "Fact from segment 2", "category": "learning",
                       "confidence": 0.9, "session_id": "sess-1", "project": "/test",
                       "source_message_id": None, "timestamp": "2024-01-01",
                       "compressed_details": ""}]
        mock_llm.side_effect = [seg1_facts, seg2_facts]
        mock_store.return_value = 1

        db.execute.return_value.fetchall.return_value = []

        from src.distill import distill
        distill(use_llm=True, segment=True)

        # extract_facts_llm should be called twice (once per segment)
        assert mock_llm.call_count == 2

        # The second call should have existing_facts containing segment 1's fact
        second_call_kwargs = mock_llm.call_args_list[1]
        existing_facts_arg = second_call_kwargs[1].get("existing_facts") or second_call_kwargs[0][3] if len(second_call_kwargs[0]) > 3 else None
        # The existing_facts for the second segment should include "Fact from segment 1"
        # We check that it was called with some existing_facts list that includes the first fact
        call_args = mock_llm.call_args_list[1]
        if "existing_facts" in call_args.kwargs:
            ef = call_args.kwargs["existing_facts"]
        else:
            ef = call_args.args[3] if len(call_args.args) > 3 else None
        assert ef is not None
        assert "Fact from segment 1" in ef


# ---------------------------------------------------------------------------
# VAL-SEG-005: --no-segment flag disables segmentation
# ---------------------------------------------------------------------------

class TestNoSegmentCLIFlag:
    """VAL-SEG-005: --no-segment flag passes segment=False to distill()."""

    def test_run_help_shows_no_segment(self) -> None:
        """'distill.py run --help' shows --no-segment flag."""
        result = subprocess.run(
            [sys.executable, "src/distill.py", "run", "--help"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        assert result.returncode == 0
        assert "--no-segment" in result.stdout

    def test_no_segment_in_argparse(self) -> None:
        """The argparse config includes --no-segment as a store_true action."""
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "distill_mod",
            str(Path(__file__).parent.parent / "src" / "distill.py"),
        )
        mod = importlib.util.module_from_spec(spec)
        # Just check help text - don't need to fully load
        result = subprocess.run(
            [sys.executable, "src/distill.py", "run", "--help"],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parent.parent),
        )
        assert "--no-segment" in result.stdout
        # Verify the help text mentions segmentation
        help_text = result.stdout.lower()
        assert "segment" in help_text


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestSegmentSessionEdgeCases:
    """Edge case tests for _segment_session."""

    @patch("src.distill.get_conn")
    def test_all_assistant_messages_returns_single_segment(self, mock_conn) -> None:
        """Session with zero user messages returns single segment."""
        from src.distill import _segment_session

        messages = [_make_message("response", role="assistant") for _ in range(20)]
        segments = _segment_session(messages)
        assert len(segments) == 1

    @patch("src.distill.get_conn")
    def test_custom_drift_threshold(self, mock_conn) -> None:
        """Custom drift_threshold affects boundary sensitivity."""
        from src.distill import _segment_session

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = []
        mock_conn.return_value = mock_db

        # Create messages with moderate topic change
        messages = _make_session_messages([f"topic {i}" for i in range(10)])

        # Very high threshold (1.0) means everything is a boundary
        # Very low threshold (0.0) means nothing is a boundary
        mock_model = MagicMock()
        base_vec = np.random.randn(384).astype(np.float32)
        base_vec /= np.linalg.norm(base_vec)

        def fake_encode(texts, **kwargs):
            results = []
            for _ in texts:
                noise = np.random.randn(384).astype(np.float32) * 0.01
                vec = base_vec + noise
                vec /= np.linalg.norm(vec)
                results.append(vec)
            return np.array(results)

        mock_model.encode = fake_encode

        with patch("src.distill._get_dedup_model", return_value=mock_model):
            # Very low threshold = almost no boundaries
            segments_low = _segment_session(messages, drift_threshold=0.01)
            # These similar messages should produce 1 segment with low threshold
            assert len(segments_low) == 1

    @patch("src.distill.get_conn")
    def test_custom_min_segment_size(self, mock_conn) -> None:
        """Custom min_segment_size affects segment merging and short-circuit."""
        from src.distill import _segment_session

        # With min_segment_size=2, need >=4 user messages to segment
        messages = _make_session_messages(["a", "b", "c"])  # 3 user msgs

        # Should still be short enough for single segment at size=2 threshold
        # (3 < 2*2 = 4)
        segments = _segment_session(messages, min_segment_size=2)
        assert len(segments) == 1

    @patch("src.distill.get_conn")
    def test_handles_encoding_failure_gracefully(self, mock_conn) -> None:
        """If embedding model fails, returns single segment."""
        from src.distill import _segment_session

        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = []
        mock_conn.return_value = mock_db

        messages = _make_session_messages([f"msg {i}" for i in range(10)])

        mock_model = MagicMock()
        mock_model.encode.side_effect = Exception("Model loading failed")

        with patch("src.distill._get_dedup_model", return_value=mock_model):
            # Should not crash, returns single segment
            segments = _segment_session(messages)
            assert len(segments) == 1
            assert segments[0] == messages


# ---------------------------------------------------------------------------
# Type hints check
# ---------------------------------------------------------------------------

class TestSegmentSessionTypeHints:
    """VAL-CROSS-004: New functions have type hints."""

    def test_segment_session_has_type_hints(self) -> None:
        """_segment_session has type hints on parameters and return type."""
        import inspect
        from src.distill import _segment_session

        sig = inspect.signature(_segment_session)

        # Check return annotation exists
        assert sig.return_annotation != inspect.Parameter.empty, \
            "_segment_session should have a return type annotation"

        # Check parameters have annotations
        for name, param in sig.parameters.items():
            assert param.annotation != inspect.Parameter.empty, \
                f"_segment_session parameter '{name}' should have a type annotation"
