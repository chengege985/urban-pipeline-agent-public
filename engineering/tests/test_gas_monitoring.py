from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.gas_application.data_package import read_package
from src.gas_application.monitoring import (
    MonitoringChannelPolicy,
    SyntheticMonitoringPolicy,
    evaluate_monitoring,
)


FIXTURE = Path(__file__).resolve().parents[1] / "experiments/gas_m0/data/gas_monitoring_replay"
START = datetime(2026, 11, 18, 1, tzinfo=UTC)
CADENCE = timedelta(minutes=1)
PRESSURE = MonitoringChannelPolicy(
    "syn-gas-pipe-K-001", "syn-gas-monitor-pressure-k001", "pressure", "kPa", 4.0, 6.0
)
FLOW = MonitoringChannelPolicy(
    "syn-gas-pipe-K-003", "syn-gas-monitor-flow-k003", "flow", "Nm3/h", 12.0, 30.0
)
POLICY = SyntheticMonitoringPolicy(
    "syn-monitoring-test-01", START, CADENCE, 3, 2, (PRESSURE, FLOW)
)


@pytest.fixture
def snapshot():
    return read_package(FIXTURE)


def _at(minute: int) -> datetime:
    return START + minute * CADENCE


def _timeline(channel):
    return [(event.kind, event.at.minute, event.cause) for event in channel.events]


def test_fixture_replays_end_to_end_with_asset_and_observation_provenance(snapshot):
    report = evaluate_monitoring(snapshot, POLICY, evaluated_at=_at(12))
    assert report.data_mode == "synthetic"
    assert report.validation_status == "not_business_validated"
    assert report.dataset_id == "syn-gas-monitoring-replay-01"
    assert report.dataset_version == snapshot.version
    assert report.manifest_sha256 == snapshot.manifest_sha256
    assert report.monitoring_source.source_sha256 == snapshot.sources["monitoring"].sha256
    assert report.policy_id == POLICY.policy_id

    pressure, flow = report.channels
    assert (pressure.asset_id, pressure.sensor_id, pressure.state) == (
        PRESSURE.asset_id, PRESSURE.sensor_id, "limit_exceeded",
    )
    assert (flow.asset_id, flow.sensor_id, flow.state) == (FLOW.asset_id, FLOW.sensor_id, "data_missing")
    assert _timeline(pressure) == [
        ("limit_exceeded", 3, "above_upper"),
        ("recovered", 4, "within_limits"),
        ("data_missing", 7, "no_usable_observation"),
        ("recovered", 8, "observations_resumed"),
        ("limit_exceeded", 12, "above_upper"),
    ]
    assert _timeline(flow) == [
        ("data_missing", 4, "no_usable_observation"),
        ("recovered", 5, "observations_resumed"),
        ("limit_exceeded", 9, "below_lower"),
        ("recovered", 10, "within_limits"),
        ("data_missing", 12, "no_usable_observation"),
    ]

    alert = pressure.events[0]
    assert [row.observed_at for row in alert.evidence] == [_at(1), _at(2), _at(3)]
    originals = {row.provenance.source_record_id: row for row in snapshot.rows.monitoring}
    for item in alert.evidence:
        original = originals[item.source_record_id]
        assert item.source_row == original.provenance.row
        assert item.source_sha256 == original.provenance.source.sha256
        assert item.quality == "valid"
    asset = next(row for row in snapshot.rows.assets if row.asset_id == pressure.asset_id)
    assert pressure.asset_evidence.source_record_id == asset.provenance.source_record_id
    assert pressure.asset_evidence.source_sha256 == snapshot.sources["assets"].sha256

    missing = flow.events[-1]
    assert missing.previous_state == "observing"
    assert missing.missing_slots == [_at(11), _at(12)]
    assert [row.observed_at for row in missing.evidence] == [_at(10)]
    assert flow.last_usable_at == _at(10)


def test_expected_channel_without_any_rows_reports_missing_without_fake_measurements(snapshot):
    without_monitoring = replace(
        snapshot,
        rows=replace(snapshot.rows, monitoring=()),
        sources={table: source for table, source in snapshot.sources.items() if table != "monitoring"},
    )
    report = evaluate_monitoring(without_monitoring, POLICY, evaluated_at=_at(3))
    assert report.monitoring_source is None
    for channel in report.channels:
        assert channel.state == "data_missing"
        assert channel.last_usable_at is None
        assert _timeline(channel) == [("data_missing", 1, "no_usable_observation")]
        assert channel.events[0].missing_slots == [_at(0), _at(1)]
        assert channel.events[0].evidence == []


def _pressure_snapshot(snapshot, slots):
    first = next(row for row in snapshot.rows.monitoring if row.sensor_id == PRESSURE.sensor_id)
    observations = tuple(
        replace(
            first,
            observed_at=_at(minute) + offset,
            value=value,
            quality=quality,
            unit=unit,
            provenance=replace(first.provenance, source_record_id=f"test-{i}", row=i + 1),
        )
        for i, (minute, offset, value, quality, unit) in enumerate(slots)
    )
    return replace(snapshot, rows=replace(snapshot.rows, monitoring=observations))


def test_gaps_direction_changes_and_future_samples_do_not_count_as_sustained(snapshot):
    slots = [
        (0, timedelta(), 7.0, "valid", "kPa"),
        (1, timedelta(), 5.0, "valid", "kPa"),
        (2, timedelta(), 7.0, "valid", "kPa"),
        (3, timedelta(), 3.0, "valid", "kPa"),
        (4, timedelta(), 7.0, "valid", "kPa"),
        (6, timedelta(), 7.0, "valid", "kPa"),
        (7, timedelta(), 7.0, "valid", "kPa"),
        (8, timedelta(), 7.0, "valid", "kPa"),
    ]
    replay = _pressure_snapshot(snapshot, slots)
    policy = replace(POLICY, channels=(PRESSURE,))
    pending = evaluate_monitoring(replay, policy, evaluated_at=_at(7)).channels[0]
    assert pending.state == "observing"
    assert pending.events == []
    confirmed = evaluate_monitoring(replay, policy, evaluated_at=_at(8)).channels[0]
    assert _timeline(confirmed) == [("limit_exceeded", 8, "above_upper")]
    assert [row.observed_at for row in confirmed.events[0].evidence] == [_at(6), _at(7), _at(8)]


def test_unusable_slots_and_restored_out_of_range_values_have_distinct_evidence(snapshot):
    slots = [
        (0, timedelta(), 5.0, "valid", "kPa"),
        (1, timedelta(), 50.0, "suspect", "kPa"),
        (2, timedelta(), 7000.0, "valid", "Pa"),
        (2, timedelta(seconds=15), 5.0, "valid", "kPa"),
        (3, timedelta(), 7.0, "valid", "kPa"),
        (4, timedelta(), 7.0, "valid", "kPa"),
        (5, timedelta(), 7.0, "valid", "kPa"),
        (6, timedelta(), 6.0, "valid", "kPa"),
    ]
    replay = _pressure_snapshot(snapshot, slots)
    policy = replace(POLICY, channels=(PRESSURE,))
    pending = evaluate_monitoring(replay, policy, evaluated_at=_at(3)).channels[0]
    assert pending.state == "observing"
    assert pending.off_schedule_observations == 1
    missing, resumed = pending.events
    assert missing.missing_slots == [_at(1), _at(2)]
    assert [(item.quality, item.unit) for item in missing.evidence] == [
        ("valid", "kPa"), ("suspect", "kPa"), ("valid", "Pa"),
    ]
    assert resumed.previous_state == "data_missing"
    assert resumed.cause == "observations_resumed"
    assert resumed.evidence[0].value == 7.0
    confirmed = evaluate_monitoring(replay, policy, evaluated_at=_at(6)).channels[0]
    assert confirmed.state == "normal"
    assert _timeline(confirmed) == [
        ("data_missing", 2, "no_usable_observation"),
        ("recovered", 3, "observations_resumed"),
        ("limit_exceeded", 5, "above_upper"),
        ("recovered", 6, "within_limits"),
    ]


def test_active_limit_is_not_cleared_by_missing_input(snapshot):
    slots = [
        (0, timedelta(), 7.0, "valid", "kPa"),
        (1, timedelta(), 7.0, "valid", "kPa"),
        (2, timedelta(), 7.0, "valid", "kPa"),
    ]
    replay = _pressure_snapshot(snapshot, slots)
    policy = replace(POLICY, channels=(PRESSURE,))
    transient = evaluate_monitoring(replay, policy, evaluated_at=_at(3)).channels[0]
    assert transient.state == "limit_exceeded"
    missing = evaluate_monitoring(replay, policy, evaluated_at=_at(4)).channels[0]
    assert missing.state == "data_missing"
    assert missing.events[-1].previous_state == "limit_exceeded"
    assert _timeline(missing) == [
        ("limit_exceeded", 2, "above_upper"), ("data_missing", 4, "no_usable_observation"),
    ]



def test_active_limit_records_an_opposite_direction_only_after_fresh_sustained_evidence(snapshot):
    readings = [(0, 7.0), (1, 7.0), (2, 7.0), (3, 3.0), (4, 7.0), (5, 3.0),
                (7, 3.0), (8, 3.0), (9, 3.0), (10, 3.0), (11, 5.0)]
    slots = [(minute, timedelta(), value, "valid", "kPa") for minute, value in readings]
    replay = _pressure_snapshot(snapshot, slots)
    policy = replace(POLICY, channels=(PRESSURE,))

    pending = evaluate_monitoring(replay, policy, evaluated_at=_at(8)).channels[0]
    assert pending.state == "limit_exceeded"
    assert _timeline(pending) == [("limit_exceeded", 2, "above_upper")]

    recovered = evaluate_monitoring(replay, policy, evaluated_at=_at(11)).channels[0]
    assert recovered.state == "normal"
    assert _timeline(recovered) == [
        ("limit_exceeded", 2, "above_upper"),
        ("limit_exceeded", 9, "below_lower"),
        ("recovered", 11, "within_limits"),
    ]
    switched = recovered.events[1]
    assert switched.previous_state == "limit_exceeded"
    assert [row.observed_at for row in switched.evidence] == [_at(7), _at(8), _at(9)]


def test_policy_cannot_evaluate_real_intake_or_misbind_an_asset(snapshot):
    with pytest.raises(ValueError, match="requires a synthetic intake snapshot"):
        evaluate_monitoring(replace(snapshot, data_mode="real_authorized"), POLICY, evaluated_at=START)
    mismatched_asset = replace(PRESSURE, asset_id=FLOW.asset_id)
    with pytest.raises(ValueError, match="disagree on asset identity"):
        evaluate_monitoring(snapshot, replace(POLICY, channels=(mismatched_asset,)), evaluated_at=START)
    unknown_asset = replace(PRESSURE, asset_id="unknown")
    with pytest.raises(ValueError, match="unknown asset"):
        evaluate_monitoring(snapshot, replace(POLICY, channels=(unknown_asset,)), evaluated_at=START)


@pytest.mark.parametrize(
    "policy",
    [
        replace(POLICY, cadence=timedelta()),
        replace(POLICY, sustained_samples=1),
        replace(POLICY, missing_samples=0),
        replace(POLICY, channels=(PRESSURE, PRESSURE)),
        replace(POLICY, channels=(replace(PRESSURE, upper=float("inf")),)),
        replace(POLICY, channels=(replace(PRESSURE, lower=8.0),)),
        replace(POLICY, channels=(replace(PRESSURE, unit="gpm"),)),
    ],
)
def test_invalid_demo_policy_is_rejected(snapshot, policy):
    with pytest.raises(ValueError, match="invalid synthetic monitoring"):
        evaluate_monitoring(snapshot, policy, evaluated_at=START)


def test_replay_requires_aware_ordered_clock_and_normalizes_offsets(snapshot):
    with pytest.raises(ValueError, match="timezone-aware"):
        evaluate_monitoring(snapshot, POLICY, evaluated_at=START.replace(tzinfo=None))
    with pytest.raises(ValueError, match="precedes"):
        evaluate_monitoring(snapshot, POLICY, evaluated_at=_at(-1))
    offset = timezone(timedelta(hours=8))
    report = evaluate_monitoring(snapshot, POLICY, evaluated_at=_at(12).astimezone(offset))
    assert report.evaluated_at.isoformat() == "2026-11-18T01:12:00+00:00"
