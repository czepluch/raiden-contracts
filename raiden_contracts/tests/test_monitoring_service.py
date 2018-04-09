import pytest
from raiden_contracts.utils.config import (
    E_CHANNEL_CLOSED
)
from .utils import check_channel_closed
from raiden_contracts.utils.sign import sign_reward_proof
from ethereum.utils import denoms


@pytest.fixture()
def create_reward_proof(token_network, get_private_key):
    def get(
            signer,
            channel_identifier,
            reward_amount,
            token_network_address,
            nonce=0,
            monitor_address=None,
            v=27
    ):
        private_key = get_private_key(signer)

        signature = sign_reward_proof(
            private_key,
            channel_identifier,
            reward_amount,
            token_network_address,
            int(token_network.call().chain_id()),
            nonce,
            monitor_address,
            v
        )
        return (
            channel_identifier,
            reward_amount,
            token_network_address,
            int(token_network.call().chain_id()),
            nonce,
            monitor_address,
            signature
        )
    return get


def test_msc_happy_path(
    token_network,
    monitoring_service_external,
    get_accounts,
    create_channel,
    create_balance_proof,
    create_reward_proof,
    event_handler,
    custom_token
):
    # setup: two parties + MS
    ev_handler = event_handler(token_network)
    (A, B, MS) = get_accounts(3)
    reward_amount = 10
    # mint some tokens
    custom_token.transact({'from': MS, 'value': 100 * denoms.finney}).mint()
    custom_token.transact({'from': A, 'value': 100 * denoms.finney}).mint()
    custom_token.transact({'from': B, 'value': 100 * denoms.finney}).mint()
    # register MS
    custom_token.transact({'from': MS}).approve(monitoring_service_external.address, 20)
    monitoring_service_external.transact({'from': MS}).depositAndRegisterMonitoringService()
    ms_balance_after_deposit = monitoring_service_external.call().balances(MS)
    # register raiden node
    custom_token.transact({'from': B}).approve(monitoring_service_external.address, 20)
    monitoring_service_external.transact({'from': B}).deposit(B, 20)

    # 1) open a channel (c1, c2)
    channel_identifier = create_channel(A, B)
    custom_token.transact({'from': A}).approve(token_network.address, 20)
    token_network.transact({'from': A}).setDeposit(channel_identifier, A, 20)
    custom_token.transact({'from': B}).approve(token_network.address, 20)
    token_network.transact({'from': B}).setDeposit(channel_identifier, B, 20)
    # 2) create balance proof
    balance_proof_A = create_balance_proof(channel_identifier, B, 10, 1)
    balance_proof_B = create_balance_proof(channel_identifier, A, 20, 2)
    balance_proof_B_nc = create_balance_proof(channel_identifier, B, 20, 2)
    # 2a) create reward proof
    reward_proof = create_reward_proof(
        A,
        channel_identifier,
        reward_amount,
        token_network.address,
        balance_proof_B[1],
        MS
    )
    # 3) c1 closes channel
    txn_hash = token_network.transact({'from': A}).closeChannel(*balance_proof_A)
    ev_handler.add(txn_hash, E_CHANNEL_CLOSED, check_channel_closed(channel_identifier, A))
    ev_handler.check()
    # 4) MS calls `MSC::monitor()` using c1's BP and reward proof

    txn_hash = monitoring_service_external.transact({'from': MS}).monitor(
        balance_proof_B[0],  # channel_id
        balance_proof_B[1],  # nonce
        balance_proof_B[2],  # transferred amount
        balance_proof_B[3],  # locksroot
        balance_proof_B[4],  # additional_hash
        balance_proof_B[5],  # closing signature
        balance_proof_B_nc[5],  # non-closing signature
        MS,                 # reward sender address
        reward_proof[6],  # reward proof signature
        token_network.address,  # token network address

    )
    # 5) MSC calls TokenNetwork updateTransfer
    # 6) channel is settled
    token_network.web3.testing.mine(8)
    token_network.transact().settleChannel(channel_identifier, A, B)
    # 7) MS claims the reward
    monitoring_service_external.transact({'from': MS}).claimReward(
        channel_identifier,
        token_network.address,
        reward_amount,
        MS
    )
    ms_balance_after_reward = monitoring_service_external.call().balances(MS)
    assert ms_balance_after_reward == (ms_balance_after_deposit + reward_amount)