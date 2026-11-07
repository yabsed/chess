import copy
import json
import os
import subprocess
import unittest
from unittest.mock import patch

from infra.bootstrap import parameters as bootstrap


ACCOUNT = '123456789012'
REGION = 'us-east-1'
EC2_ARN = f'arn:aws:ec2:{REGION}:{ACCOUNT}'
ELB_ARN = f'arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}'
GROUP_ARN = f'arn:aws:autoscaling:{REGION}:{ACCOUNT}:autoScalingGroup:uuid:autoScalingGroupName/chess-asg'
SECRET_ARN = f'arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:database-url-suffix'


class BootstrapParametersTests(unittest.TestCase):
    def setUp(self):
        environment = {
            'AWS_REGION': REGION,
            'AWS_ACCOUNT_ID': ACCOUNT,
            'FOUNDATION_STACK': 'chess-foundation',
        }
        self.enterContext(patch.dict(os.environ, environment, clear=True))

        resources = {
            'InternetGateway':           ('EC2::InternetGateway', 'igw-01'),
            'ECSLaunchTemplate':         ('EC2::LaunchTemplate', 'lt-01'),
            'NatGateway':                ('EC2::NatGateway', 'nat-01'),
            'PublicRoutes':              ('EC2::RouteTable', 'rtb-01'),
            'PrivateRoutesA':            ('EC2::RouteTable', 'rtb-02'),
            'PrivateRoutesB':            ('EC2::RouteTable', 'rtb-03'),
            'LoadBalancerSecurityGroup': ('EC2::SecurityGroup', 'sg-01'),
            'BackendSecurityGroup':      ('EC2::SecurityGroup', 'sg-02'),
            'DatabaseSecurityGroup':     ('EC2::SecurityGroup', 'sg-03'),
            'PublicSubnetA':             ('EC2::Subnet', 'subnet-01'),
            'PublicSubnetB':             ('EC2::Subnet', 'subnet-02'),
            'PrivateSubnetA':            ('EC2::Subnet', 'subnet-03'),
            'PrivateSubnetB':            ('EC2::Subnet', 'subnet-04'),
            'S3Endpoint':                ('EC2::VPCEndpoint', 'vpce-01'),
            'VPC':                       ('EC2::VPC', 'vpc-01'),
            'NatAddress':                ('EC2::EIP', '192.0.2.1'),
            'ECSAutoScalingGroup':       ('AutoScaling::AutoScalingGroup', 'chess-asg'),
            'ApiDistribution':           ('CloudFront::Distribution', 'distribution-01'),
            'ApiOrigin':                 ('CloudFront::VpcOrigin', 'origin-01'),
            'Listener':                  ('ElasticLoadBalancingV2::Listener', f'{ELB_ARN}:listener/app/01'),
            'LoadBalancer':              ('ElasticLoadBalancingV2::LoadBalancer', f'{ELB_ARN}:loadbalancer/app/01'),
            'TargetGroup':               ('ElasticLoadBalancingV2::TargetGroup', f'{ELB_ARN}:targetgroup/01'),
            'DatabaseUrl':               ('SecretsManager::Secret', SECRET_ARN),
            'AsgCapacityProvider':       ('ECS::CapacityProvider', 'chess-capacity'),
        }
        self.resources = {
            logical_id: {
                'LogicalResourceId': logical_id,
                'ResourceType': f'AWS::{resource_type}',
                'PhysicalResourceId': physical_id,
            }
            for logical_id, (resource_type, physical_id) in resources.items()
        }
        self.responses = {
            ('sts', 'get-caller-identity'): {
                'Account': ACCOUNT,
                'Arn': f'arn:aws:iam::{ACCOUNT}:user/operator',
            },
            ('cloudformation', 'list-stack-resources'): {
                'StackResourceSummaries': list(self.resources.values()),
            },
            ('ec2', 'describe-addresses'): {
                'Addresses': [{'PublicIp': '192.0.2.1', 'AllocationId': 'eipalloc-abc123'}],
            },
            ('autoscaling', 'describe-auto-scaling-groups'): {
                'AutoScalingGroups': [{
                    'AutoScalingGroupName': 'chess-asg',
                    'AutoScalingGroupARN': GROUP_ARN,
                }],
            },
        }
        self.run = self.enterContext(patch.object(subprocess, 'run', side_effect=self.read_aws))

    def read_aws(self, command, **options):
        self.assertEqual(command[0], 'aws')
        self.assertEqual(command[-5:], ['--region', REGION, '--output', 'json', '--no-cli-pager'])
        self.assertEqual(options, {'check': True, 'capture_output': True, 'text': True})
        response = self.responses[tuple(command[1:3])]
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(response))

    def test_outputs_keep_exact_parameter_order_and_resource_scopes(self):
        result = bootstrap.parameters()

        network_ids = [
            'elastic-ip/eipalloc-abc123',
            'internet-gateway/igw-01',
            'launch-template/lt-01',
            'natgateway/nat-01',
            'route-table/rtb-01',
            'route-table/rtb-02',
            'route-table/rtb-03',
            'security-group/sg-01',
            'security-group/sg-02',
            'security-group/sg-03',
            'subnet/subnet-01',
            'subnet/subnet-02',
            'subnet/subnet-03',
            'subnet/subnet-04',
            'vpc-endpoint/vpce-01',
            'vpc/vpc-01',
        ]
        expected = {
            'FoundationStack': 'chess-foundation',
            'AutoScalingGroupArn': GROUP_ARN,
            'CloudFrontResources': (
                f'arn:aws:cloudfront::{ACCOUNT}:distribution/distribution-01,'
                f'arn:aws:cloudfront::{ACCOUNT}:vpcorigin/origin-01'
            ),
            'NetworkResources': ','.join(f'{EC2_ARN}:{resource_id}' for resource_id in network_ids),
            'CapacityProviderName': 'chess-capacity',
            'LoadBalancerResources': (
                f'{ELB_ARN}:listener/app/01,'
                f'{ELB_ARN}:loadbalancer/app/01,'
                f'{ELB_ARN}:targetgroup/01'
            ),
            'DatabaseSecretArn': SECRET_ARN,
            'LaunchTemplateId': 'lt-01',
            'BackendSecurityGroupId': 'sg-02',
            'PrivateSubnetA': 'subnet-03',
            'PrivateSubnetB': 'subnet-04',
        }
        self.assertEqual(result, [
            {'ParameterKey': key, 'ParameterValue': value}
            for key, value in expected.items()
        ])
        self.assertEqual([entry.args[0][1:-5] for entry in self.run.call_args_list], [
            ['sts', 'get-caller-identity'],
            ['cloudformation', 'list-stack-resources', '--stack-name', 'chess-foundation'],
            ['ec2', 'describe-addresses', '--public-ips', '192.0.2.1'],
            ['autoscaling', 'describe-auto-scaling-groups', '--auto-scaling-group-names', 'chess-asg'],
        ])

    def test_unlisted_resources_never_expand_the_network_allowlist(self):
        expected = bootstrap.parameters()
        rows = self.responses[('cloudformation', 'list-stack-resources')]['StackResourceSummaries']
        rows.append({
            'LogicalResourceId': 'UnlistedSecurityGroup',
            'ResourceType': 'AWS::EC2::SecurityGroup',
            'PhysicalResourceId': 'sg-unlisted',
        })

        self.assertEqual(bootstrap.parameters(), expected)

    def test_missing_settings_and_wrong_account_stop_before_resource_reads(self):
        with patch.dict(os.environ, {'AWS_REGION': ''}):
            with self.assertRaisesRegex(ValueError, 'Missing deployment settings: AWS_REGION'):
                bootstrap.parameters()
        self.run.assert_not_called()

        self.responses[('sts', 'get-caller-identity')]['Account'] = '999999999999'
        with self.assertRaisesRegex(ValueError, 'account does not match'):
            bootstrap.parameters()
        self.assertEqual(self.run.call_count, 1)

    def test_missing_wrong_type_and_unsafe_resource_ids_are_rejected(self):
        row = self.resources['VPC']
        original = copy.deepcopy(row)
        changes = [
            {'LogicalResourceId': 'DifferentVPC'},
            {'ResourceType': 'AWS::EC2::Subnet'},
            {'PhysicalResourceId': '*'},
            {'PhysicalResourceId': 'vpc-01,vpc-02'},
            {'PhysicalResourceId': ''},
        ]
        for change in changes:
            with self.subTest(change=change):
                row.clear()
                row.update(original)
                row.update(change)
                with self.assertRaisesRegex(ValueError, 'unexpected foundation resource: VPC'):
                    bootstrap.parameters()

    def test_nat_resolution_requires_one_matching_address_and_allocation(self):
        addresses = self.responses[('ec2', 'describe-addresses')]
        invalid_addresses = [
            [],
            [{'PublicIp': '192.0.2.2', 'AllocationId': 'eipalloc-abc123'}],
            [{'PublicIp': '192.0.2.1'}],
            [{'PublicIp': '192.0.2.1', 'AllocationId': '*'}],
            [{'PublicIp': '192.0.2.1', 'AllocationId': 'eipalloc-abc123'}] * 2,
        ]
        for rows in invalid_addresses:
            with self.subTest(rows=rows):
                addresses['Addresses'] = rows
                with self.assertRaises(ValueError):
                    bootstrap.parameters()

    def test_auto_scaling_resolution_requires_exact_name_and_scoped_arn(self):
        groups = self.responses[('autoscaling', 'describe-auto-scaling-groups')]
        valid_group = groups['AutoScalingGroups'][0]
        invalid_groups = [
            [],
            [valid_group, valid_group],
            [dict(valid_group, AutoScalingGroupName='other-asg')],
            [dict(valid_group, AutoScalingGroupARN=GROUP_ARN.replace(ACCOUNT, '999999999999'))],
            [dict(valid_group, AutoScalingGroupARN=GROUP_ARN.replace(REGION, 'us-west-2'))],
            [dict(valid_group, AutoScalingGroupARN=GROUP_ARN.replace('chess-asg', 'other-asg'))],
            [dict(valid_group, AutoScalingGroupARN=GROUP_ARN + '*')],
        ]
        for rows in invalid_groups:
            with self.subTest(rows=rows):
                groups['AutoScalingGroups'] = rows
                with self.assertRaises(ValueError):
                    bootstrap.parameters()

    def test_elb_and_secret_arns_reject_other_accounts_and_wildcards(self):
        for logical_id in ('Listener', 'LoadBalancer', 'TargetGroup', 'DatabaseUrl'):
            row = self.resources[logical_id]
            expected_arn = row['PhysicalResourceId']
            invalid_arns = [
                expected_arn.replace(ACCOUNT, '999999999999'),
                expected_arn.replace(REGION, 'us-west-2'),
                expected_arn + '*',
            ]
            for invalid_arn in invalid_arns:
                with self.subTest(logical_id=logical_id, arn=invalid_arn):
                    row['PhysicalResourceId'] = invalid_arn
                    with self.assertRaises(ValueError):
                        bootstrap.parameters()
            row['PhysicalResourceId'] = expected_arn

    def test_aws_read_failure_is_not_replaced_by_partial_parameters(self):
        error = subprocess.CalledProcessError(1, ['aws', 'sts', 'get-caller-identity'])
        self.run.side_effect = error

        with self.assertRaises(subprocess.CalledProcessError) as failure:
            bootstrap.parameters()

        self.assertIs(failure.exception, error)


if __name__ == '__main__':
    unittest.main()
