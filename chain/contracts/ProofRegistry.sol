// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract ProofRegistry {
    struct Proof {
        address submitter;
        uint256 timestamp;
    }

    mapping(bytes32 => Proof) public proofs;

    event ProofRecorded(bytes32 indexed proofHash, address indexed submitter, uint256 timestamp);

    function recordProof(bytes32 proofHash) external {
        require(proofs[proofHash].timestamp == 0, "Proof already exists");

        proofs[proofHash] = Proof({
            submitter: msg.sender,
            timestamp: block.timestamp
        });

        emit ProofRecorded(proofHash, msg.sender, block.timestamp);
    }
}