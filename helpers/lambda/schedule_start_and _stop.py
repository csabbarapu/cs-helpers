import logging
import os
from datetime import datetime

import boto3
import pytz

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Environment configuration
TIMEZONE = os.getenv("TIMEZONE", "Europe/Amsterdam")
ALLOWED_ENVS = set(
    env.strip().lower() for env in os.getenv("ALLOWED_ENVS", "d,t,a").split(",")
)

START_HOUR = int(os.getenv("START_HOUR", "8"))
STOP_HOUR = int(os.getenv("STOP_HOUR", "19"))


def current_hour():
    return datetime.now(pytz.timezone(TIMEZONE)).hour


def should_stop():
    return current_hour() == STOP_HOUR


def should_start():
    return current_hour() == START_HOUR


def matches_env(tags):
    if not tags:
        return False
    for tag in tags:
        if tag["Key"].lower() == "environment" and tag["Value"].lower() in ALLOWED_ENVS:
            return True
    return False


def manage_ec2(ec2):
    """
    Manages EC2 instances by starting or stopping them based on environment tags and scheduling logic.

    Args:
        ec2 (boto3.client): The boto3 EC2 client used to interact with AWS EC2 instances.

    Behavior:
        - Iterates over all EC2 instances filtered by the states 'running' or 'stopped'.
        - For each instance, checks if it matches the required environment using `matches_env(tags)`.
        - If `should_stop()` returns True and the instance is running, schedules it to be stopped.
        - If `should_start()` returns True and the instance is stopped, schedules it to be started.
        - Logs and performs the start/stop actions on the selected instances.

    Note:
        The functions `matches_env`, `should_stop`, and `should_start` are expected to be defined elsewhere.
    """
    logger.info("Managing EC2 instances")
    paginator = ec2.get_paginator("describe_instances")
    page_iterator = paginator.paginate(
        Filters=[
            {"Name": "instance-state-name", "Values": ["running", "stopped"]},
        ]
    )
    to_stop, to_start = [], []
    for page in page_iterator:
        for reservation in page["Reservations"]:
            for instance in reservation["Instances"]:
                instance_id = instance["InstanceId"]
                state = instance["State"]["Name"]
                tags = instance.get("Tags", [])
                if matches_env(tags):
                    if should_stop() and state == "running":
                        to_stop.append(instance_id)
                    elif should_start() and state == "stopped":
                        to_start.append(instance_id)
    if to_stop:
        logger.info(f"Stopping EC2 instances: {to_stop}")
        ec2.stop_instances(InstanceIds=to_stop)
    if to_start:
        logger.info(f"Starting EC2 instances: {to_start}")
        ec2.start_instances(InstanceIds=to_start)


def manage_rds(rds):
    """
    Manages the start and stop operations for AWS RDS instances based on environment tags and status.

    Args:
        rds (boto3.client): The boto3 RDS client used to interact with AWS RDS service.

    Behavior:
        - Iterates over all RDS instances.
        - For each instance, retrieves its ARN, tags, identifier, and status.
        - If the instance matches the required environment tags:
            - Stops the instance if it is available and should be stopped.
            - Starts the instance if it is stopped and should be started.
        - Logs actions taken for each instance.

    Note:
        The functions `matches_env`, `should_stop`, and `should_start` are expected to be defined elsewhere.
    """
    logger.info("Managing RDS instances")
    dbs = rds.describe_db_instances()["DBInstances"]
    for db in dbs:
        arn = db["DBInstanceArn"]
        tags = rds.list_tags_for_resource(ResourceName=arn)["TagList"]
        db_id = db["DBInstanceIdentifier"]
        status = db["DBInstanceStatus"]
        if matches_env(tags):
            if should_stop() and status == "available":
                logger.info(f"Stopping RDS DB: {db_id}")
                rds.stop_db_instance(DBInstanceIdentifier=db_id)
            elif should_start() and status == "stopped":
                logger.info(f"Starting RDS DB: {db_id}")
                rds.start_db_instance(DBInstanceIdentifier=db_id)


def manage_ecs(ecs):
    """
    Manages the scaling of ECS Fargate services based on environment tags and scheduling logic.

    This function iterates through all ECS clusters and their services, checking for services
    that match specific environment tags. Depending on the scheduling logic (as determined by
    the `should_stop()` and `should_start()` functions), it scales services down to zero or
    back up to their desired count (as specified by the "desiredCount" tag or defaults to 1).

    Args:
        ecs: A boto3 ECS client instance used to interact with AWS ECS services.

    Side Effects:
        - Updates the desired count of ECS services to start or stop them.
        - Logs scaling actions for each affected service.

    Dependencies:
        - logger: Logger instance for logging actions.
        - matches_env(tags): Function to filter services by environment tags.
        - should_stop(): Function that determines if services should be stopped.
        - should_start(): Function that determines if services should be started.
    """
    logger.info("Managing ECS Fargate services")
    clusters = ecs.list_clusters()["clusterArns"]
    for cluster_arn in clusters:
        services = ecs.list_services(cluster=cluster_arn)["serviceArns"]
        for service_arn in services:
            service = ecs.describe_services(
                cluster=cluster_arn, services=[service_arn]
            )["services"][0]
            tags = ecs.list_tags_for_resource(resourceArn=service_arn)["tags"]
            if matches_env(tags):
                desired_count = service.get("desiredCount", 0)
                if should_stop() and desired_count > 0:
                    logger.info(
                        f"Scaling down ECS service {service_arn} in {cluster_arn}"
                    )
                    ecs.update_service(
                        cluster=cluster_arn, service=service_arn, desiredCount=0
                    )
                elif should_start() and desired_count == 0:
                    desired = int(
                        next(
                            (t["Value"] for t in tags if t["Key"] == "desiredCount"), 1
                        )
                    )
                    logger.info(
                        f"Scaling up ECS service {service_arn} in {cluster_arn} to {desired}"
                    )
                    ecs.update_service(
                        cluster=cluster_arn, service=service_arn, desiredCount=desired
                    )


def lambda_handler(event=None, context=None):
    logger.info(
        f"Scheduler triggered at {datetime.now(pytz.timezone(TIMEZONE)).strftime('%Y-%m-%d %H:%M:%S %Z')}"
    )
    ec2 = boto3.client("ec2")
    rds = boto3.client("rds")
    ecs = boto3.client("ecs")

    for manager, name in [
        (manage_ec2, "EC2"),
        (manage_rds, "RDS"),
        (manage_ecs, "ECS"),
    ]:
        try:
            manager(locals()[name.lower()])
        except Exception as e:
            logger.error(f"Error managing {name}: {e}", exc_info=True)


if __name__ == "__main__":
    lambda_handler()
