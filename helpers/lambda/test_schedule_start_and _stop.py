import pytest
from unittest import mock

from schedule_start_and _stop import (
    matches_env,
    should_start,
    should_stop,
    manage_ec2,
    manage_rds,
    manage_ecs,
)

@pytest.fixture(autouse=True)
def patch_env(monkeypatch):
    monkeypatch.setenv("TIMEZONE", "UTC")
    monkeypatch.setenv("ALLOWED_ENVS", "d,t,a")
    monkeypatch.setenv("START_HOUR", "8")
    monkeypatch.setenv("STOP_HOUR", "19")

def test_matches_env_true():
    tags = [{"Key": "Environment", "Value": "d"}]
    assert matches_env(tags) is True

def test_matches_env_false_wrong_key():
    tags = [{"Key": "Other", "Value": "d"}]
    assert matches_env(tags) is False

def test_matches_env_false_wrong_value():
    tags = [{"Key": "Environment", "Value": "prod"}]
    assert matches_env(tags) is False

def test_matches_env_empty():
    assert matches_env([]) is False
    assert matches_env(None) is False

@mock.patch("schedule_start_and _stop.current_hour")
def test_should_start(mock_hour):
    mock_hour.return_value = 8
    assert should_start() is True
    mock_hour.return_value = 7
    assert should_start() is False

@mock.patch("schedule_start_and _stop.current_hour")
def test_should_stop(mock_hour):
    mock_hour.return_value = 19
    assert should_stop() is True
    mock_hour.return_value = 18
    assert should_stop() is False

def make_instance(instance_id, state, tags):
    return {
        "InstanceId": instance_id,
        "State": {"Name": state},
        "Tags": tags,
    }

@mock.patch("schedule_start_and _stop.should_stop", return_value=True)
@mock.patch("schedule_start_and _stop.should_start", return_value=False)
def test_manage_ec2_stop(mock_start, mock_stop):
    ec2 = mock.Mock()
    paginator = mock.Mock()
    ec2.get_paginator.return_value = paginator
    paginator.paginate.return_value = [
        {
            "Reservations": [
                {
                    "Instances": [
                        make_instance("i-1", "running", [{"Key": "Environment", "Value": "d"}]),
                        make_instance("i-2", "stopped", [{"Key": "Environment", "Value": "d"}]),
                        make_instance("i-3", "running", [{"Key": "Other", "Value": "d"}]),
                    ]
                }
            ]
        }
    ]
    manage_ec2(ec2)
    ec2.stop_instances.assert_called_once_with(InstanceIds=["i-1"])
    ec2.start_instances.assert_not_called()

@mock.patch("schedule_start_and _stop.should_stop", return_value=False)
@mock.patch("schedule_start_and _stop.should_start", return_value=True)
def test_manage_ec2_start(mock_start, mock_stop):
    ec2 = mock.Mock()
    paginator = mock.Mock()
    ec2.get_paginator.return_value = paginator
    paginator.paginate.return_value = [
        {
            "Reservations": [
                {
                    "Instances": [
                        make_instance("i-1", "stopped", [{"Key": "Environment", "Value": "d"}]),
                        make_instance("i-2", "running", [{"Key": "Environment", "Value": "d"}]),
                    ]
                }
            ]
        }
    ]
    manage_ec2(ec2)
    ec2.start_instances.assert_called_once_with(InstanceIds=["i-1"])
    ec2.stop_instances.assert_not_called()

@mock.patch("schedule_start_and _stop.should_stop", return_value=True)
@mock.patch("schedule_start_and _stop.should_start", return_value=False)
def test_manage_rds_stop(mock_start, mock_stop):
    rds = mock.Mock()
    rds.describe_db_instances.return_value = {
        "DBInstances": [
            {
                "DBInstanceArn": "arn:db:1",
                "DBInstanceIdentifier": "db1",
                "DBInstanceStatus": "available",
            },
            {
                "DBInstanceArn": "arn:db:2",
                "DBInstanceIdentifier": "db2",
                "DBInstanceStatus": "stopped",
            },
        ]
    }
    rds.list_tags_for_resource.side_effect = [
        {"TagList": [{"Key": "Environment", "Value": "d"}]},
        {"TagList": [{"Key": "Other", "Value": "d"}]},
    ]
    manage_rds(rds)
    rds.stop_db_instance.assert_called_once_with(DBInstanceIdentifier="db1")
    rds.start_db_instance.assert_not_called()

@mock.patch("schedule_start_and _stop.should_stop", return_value=False)
@mock.patch("schedule_start_and _stop.should_start", return_value=True)
def test_manage_rds_start(mock_start, mock_stop):
    rds = mock.Mock()
    rds.describe_db_instances.return_value = {
        "DBInstances": [
            {
                "DBInstanceArn": "arn:db:1",
                "DBInstanceIdentifier": "db1",
                "DBInstanceStatus": "stopped",
            }
        ]
    }
    rds.list_tags_for_resource.return_value = {
        "TagList": [{"Key": "Environment", "Value": "d"}]
    }
    manage_rds(rds)
    rds.start_db_instance.assert_called_once_with(DBInstanceIdentifier="db1")
    rds.stop_db_instance.assert_not_called()

@mock.patch("schedule_start_and _stop.should_stop", return_value=True)
@mock.patch("schedule_start_and _stop.should_start", return_value=False)
def test_manage_ecs_stop(mock_start, mock_stop):
    ecs = mock.Mock()
    ecs.list_clusters.return_value = {"clusterArns": ["c1"]}
    ecs.list_services.return_value = {"serviceArns": ["s1"]}
    ecs.describe_services.return_value = {
        "services": [{"desiredCount": 2}]
    }
    ecs.list_tags_for_resource.return_value = {
        "tags": [{"Key": "Environment", "Value": "d"}]
    }
    manage_ecs(ecs)
    ecs.update_service.assert_called_once_with(
        cluster="c1", service="s1", desiredCount=0
    )

@mock.patch("schedule_start_and _stop.should_stop", return_value=False)
@mock.patch("schedule_start_and _stop.should_start", return_value=True)
def test_manage_ecs_start_with_desired_count_tag(mock_start, mock_stop):
    ecs = mock.Mock()
    ecs.list_clusters.return_value = {"clusterArns": ["c1"]}
    ecs.list_services.return_value = {"serviceArns": ["s1"]}
    ecs.describe_services.return_value = {
        "services": [{"desiredCount": 0}]
    }
    ecs.list_tags_for_resource.return_value = {
        "tags": [
            {"Key": "Environment", "Value": "d"},
            {"Key": "desiredCount", "Value": "3"},
        ]
    }
    manage_ecs(ecs)
    ecs.update_service.assert_called_once_with(
        cluster="c1", service="s1", desiredCount=3
    )

@mock.patch("schedule_start_and _stop.should_stop", return_value=False)
@mock.patch("schedule_start_and _stop.should_start", return_value=True)
def test_manage_ecs_start_with_default_desired_count(mock_start, mock_stop):
    ecs = mock.Mock()
    ecs.list_clusters.return_value = {"clusterArns": ["c1"]}
    ecs.list_services.return_value = {"serviceArns": ["s1"]}
    ecs.describe_services.return_value = {
        "services": [{"desiredCount": 0}]
    }
    ecs.list_tags_for_resource.return_value = {
        "tags": [{"Key": "Environment", "Value": "d"}]
    }
    manage_ecs(ecs)
    ecs.update_service.assert_called_once_with(
        cluster="c1", service="s1", desiredCount=1
    )

