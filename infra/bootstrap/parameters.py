"""Read the existing foundation and print parameters for bootstrap/access.yaml."""

import json
import os
import re
import subprocess


def parameters():
    required = ('AWS_REGION', 'AWS_ACCOUNT_ID', 'FOUNDATION_STACK')
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise ValueError(f'Missing deployment settings: {", ".join(missing)}')

    region  = os.environ['AWS_REGION']
    account = os.environ['AWS_ACCOUNT_ID']
    stack   = os.environ['FOUNDATION_STACK']

    def aws(*args):
        result = subprocess.run(
            ['aws', *args, '--region', region, '--output', 'json', '--no-cli-pager'],
            check=True,
            capture_output=True,
            text=True,
        )
        return json.loads(result.stdout)

    identity = aws('sts', 'get-caller-identity')
    if identity['Account'] != account:
        raise ValueError('Authenticated AWS account does not match AWS_ACCOUNT_ID.')
    partition = identity['Arn'].split(':')[1]

    response = aws('cloudformation', 'list-stack-resources', '--stack-name', stack)
    resources = {row['LogicalResourceId']: row for row in response['StackResourceSummaries']}

    def physical(logical_id, resource_type):
        row = resources.get(logical_id, {})
        value = row.get('PhysicalResourceId', '')
        if row.get('ResourceType') != f'AWS::{resource_type}' or not re.fullmatch(
            r'[A-Za-z0-9:/_.+=@-]+', value
        ):
            raise ValueError(f'Missing or unexpected foundation resource: {logical_id}')
        return value

    def arn(service, resource, global_service=False):
        scope = '' if global_service else region
        return f'arn:{partition}:{service}:{scope}:{account}:{resource}'

    def checked_arn(value, service):
        if not value.startswith(arn(service, '')) or not re.fullmatch(
            r'[A-Za-z0-9:/_.+=@-]+', value
        ):
            raise ValueError(f'Unexpected {service} resource ARN.')
        return value

    # Only these logical IDs may enter the network update allowlist.
    network = (
        # Logical ID                  CFN type (EC2)      ARN resource type
        ('InternetGateway',           'InternetGateway', 'internet-gateway'),
        ('ECSLaunchTemplate',         'LaunchTemplate',  'launch-template'),
        ('NatGateway',                'NatGateway',      'natgateway'),
        ('PublicRoutes',              'RouteTable',      'route-table'),
        ('PrivateRoutesA',            'RouteTable',      'route-table'),
        ('PrivateRoutesB',            'RouteTable',      'route-table'),
        ('LoadBalancerSecurityGroup', 'SecurityGroup',   'security-group'),
        ('BackendSecurityGroup',      'SecurityGroup',   'security-group'),
        ('DatabaseSecurityGroup',     'SecurityGroup',   'security-group'),
        ('PublicSubnetA',             'Subnet',          'subnet'),
        ('PublicSubnetB',             'Subnet',          'subnet'),
        ('PrivateSubnetA',            'Subnet',          'subnet'),
        ('PrivateSubnetB',            'Subnet',          'subnet'),
        ('S3Endpoint',                'VPCEndpoint',     'vpc-endpoint'),
        ('VPC',                       'VPC',             'vpc'),
    )
    network_arns = []
    for logical_id, resource_type, arn_type in network:
        resource_id = physical(logical_id, f'EC2::{resource_type}')
        resource_arn = arn('ec2', f'{arn_type}/{resource_id}')
        network_arns.append(resource_arn)

    # CloudFormation records the EIP address, while IAM needs its allocation ID.
    public_ip = physical('NatAddress', 'EC2::EIP')
    address_response = aws('ec2', 'describe-addresses', '--public-ips', public_ip)
    addresses = address_response['Addresses']
    if len(addresses) != 1 or addresses[0].get('PublicIp') != public_ip:
        raise ValueError('Could not resolve the foundation NAT address.')
    allocation_id = addresses[0].get('AllocationId', '')
    if not re.fullmatch(r'eipalloc-[a-f0-9]+', allocation_id):
        raise ValueError('Missing NAT address allocation ID.')
    address_arn = arn('ec2', f'elastic-ip/{allocation_id}')
    network_arns.append(address_arn)

    # Its generated UUID is part of the ARN and cannot be derived from the name.
    group_name = physical('ECSAutoScalingGroup', 'AutoScaling::AutoScalingGroup')
    group_response = aws(
        'autoscaling', 'describe-auto-scaling-groups', '--auto-scaling-group-names', group_name,
    )
    groups = group_response['AutoScalingGroups']
    if len(groups) != 1 or groups[0].get('AutoScalingGroupName') != group_name:
        raise ValueError('Could not resolve the foundation Auto Scaling group.')
    group_arn = checked_arn(groups[0]['AutoScalingGroupARN'], 'autoscaling')
    if not group_arn.endswith(f':autoScalingGroupName/{group_name}'):
        raise ValueError('Auto Scaling group ARN does not match its name.')

    distribution = physical('ApiDistribution', 'CloudFront::Distribution')
    origin = physical('ApiOrigin', 'CloudFront::VpcOrigin')
    distribution_arn = arn('cloudfront', f'distribution/{distribution}', global_service=True)
    origin_arn = arn('cloudfront', f'vpcorigin/{origin}', global_service=True)

    load_balancer_arns = []
    for name in ('Listener', 'LoadBalancer', 'TargetGroup'):
        resource_id = physical(name, f'ElasticLoadBalancingV2::{name}')
        resource_arn = checked_arn(resource_id, 'elasticloadbalancing')
        load_balancer_arns.append(resource_arn)

    secret_id = physical('DatabaseUrl', 'SecretsManager::Secret')
    secret_arn = checked_arn(secret_id, 'secretsmanager')

    cloudfront_resources = ','.join([distribution_arn, origin_arn])
    network_arns.sort()
    network_resources = ','.join(network_arns)
    capacity_provider = physical('AsgCapacityProvider', 'ECS::CapacityProvider')
    load_balancer_resources = ','.join(load_balancer_arns)
    launch_template = physical('ECSLaunchTemplate', 'EC2::LaunchTemplate')
    backend_security_group = physical('BackendSecurityGroup', 'EC2::SecurityGroup')
    private_subnet_a = physical('PrivateSubnetA', 'EC2::Subnet')
    private_subnet_b = physical('PrivateSubnetB', 'EC2::Subnet')

    values = {
        'FoundationStack':        stack,
        'AutoScalingGroupArn':    group_arn,
        'CloudFrontResources':    cloudfront_resources,
        'NetworkResources':       network_resources,
        'CapacityProviderName':   capacity_provider,
        'LoadBalancerResources':  load_balancer_resources,
        'DatabaseSecretArn':      secret_arn,
        'LaunchTemplateId':       launch_template,
        'BackendSecurityGroupId': backend_security_group,
        'PrivateSubnetA':         private_subnet_a,
        'PrivateSubnetB':         private_subnet_b,
    }
    return [{'ParameterKey': key, 'ParameterValue': value} for key, value in values.items()]


if __name__ == '__main__':
    try:
        print(json.dumps(parameters(), indent=2))
    except (KeyError, ValueError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(str(error))
