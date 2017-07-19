import base64
import random
import string
from binascii import hexlify
from collections import OrderedDict

import itertools
import pytest
from ledger.compact_merkle_tree import CompactMerkleTree
from ledger.hash_stores.file_hash_store import FileHashStore
from ledger.ledger import Ledger
from ledger.serializers.compact_serializer import CompactSerializer
from ledger.serializers.msgpack_serializer import MsgPackSerializer
from ledger.test.conftest import orderedFields
from ledger.test.helper import NoTransactionRecoveryLedger, \
    check_ledger_generator, create_ledger_text_file_storage, create_ledger_chunked_file_storage, \
    create_ledger_leveldb_file_storage, create_default_ledger
from ledger.test.test_file_hash_store import generateHashes
from ledger.util import ConsistencyVerificationFailed, F
from storage.text_file_store import TextFileStore


def b64e(s):
    return base64.b64encode(s).decode("utf-8")


def b64d(s):
    return base64.b64decode(s)


def lst2str(l):
    return ",".join(l)


def test_add_txn(ledger, genesis_txns, genesis_txn_file):
    offset = len(genesis_txns) if genesis_txn_file else 0
    txn1 = {
        'identifier': 'cli1',
        'reqId': 1,
        'op': 'do something'
    }
    ledger.add(txn1)

    txn2 = {
        'identifier': 'cli1',
        'reqId': 2,
        'op': 'do something else'
    }
    ledger.add(txn2)

    # Check that the transaction is added to the Merkle Tree
    assert ledger.size == 2 + offset

    # Check that the data is appended to the immutable store
    txn1[F.seqNo.name] = 1
    txn2[F.seqNo.name] = 2
    assert sorted(txn1) == sorted(ledger[1])
    assert sorted(txn2) == sorted(ledger[2])
    check_ledger_generator(ledger)


def test_query_merkle_info(ledger, genesis_txns, genesis_txn_file):
    offset = len(genesis_txns) if genesis_txn_file else 0
    merkleInfo = {}
    for i in range(100):
        txn = {
            'identifier': 'cli' + str(i),
            'reqId': i + 1,
            'op': ''.join([random.choice(string.printable) for i in range(
                random.randint(i + 1, 100))])
        }
        mi = ledger.add(txn)
        seqNo = mi.pop(F.seqNo.name)
        assert i + 1 + offset == seqNo
        merkleInfo[seqNo] = mi
        print(mi)

    for i in range(100):
        assert sorted(merkleInfo[i + 1 + offset]) == sorted(ledger.merkleInfo(i + 1 + offset))


"""
If the server holding the ledger restarts, the ledger should be fully rebuilt
from persisted data. Any incoming commands should be stashed. (Does this affect
creation of Signed Tree Heads? I think I don't really understand what STHs are.)
"""


def test_recover_merkle_tree_from_txn_log_text_file(tempdir, serializer, genesis_txn_file):
    check_recover_merkle_tree_from_txn_log(create_ledger_text_file_storage,
                                                     tempdir, serializer, genesis_txn_file)

def test_recover_merkle_tree_from_txn_log_chunked_file(tempdir, serializer, genesis_txn_file):
    check_recover_merkle_tree_from_txn_log(create_ledger_chunked_file_storage,
                                                     tempdir, serializer, genesis_txn_file)

def test_recover_merkle_tree_from_txn_log_leveldb_file(tempdir, serializer, genesis_txn_file):
    check_recover_merkle_tree_from_txn_log(create_ledger_leveldb_file_storage,
                                                     tempdir, serializer, genesis_txn_file)

def check_recover_merkle_tree_from_txn_log(create_ledger_func, tempdir, serializer, genesis_txn_file):
    ledger = create_ledger_func(serializer, tempdir, genesis_txn_file)
    for d in range(100):
        txn = {
            'identifier': 'cli' + str(d),
            'reqId': d + 1,
            'op': 'do something'
        }
        ledger.add(txn)
    ledger.stop()
    # delete hash store, so that the only option for recovering is txn log
    ledger.tree.hashStore.reset()

    size_before = ledger.size
    tree_root_hash_before = ledger.tree.root_hash
    tree_size_before = ledger.tree.tree_size
    root_hash_before = ledger.root_hash
    hashes_before = ledger.tree.hashes

    restartedLedger = create_ledger_func(serializer, tempdir, genesis_txn_file)

    assert size_before == restartedLedger.size
    assert root_hash_before == restartedLedger.root_hash
    assert hashes_before == restartedLedger.tree.hashes
    assert tree_root_hash_before == restartedLedger.tree.root_hash
    assert tree_size_before == restartedLedger.tree.tree_size


def test_recover_merkle_tree_from_hash_store(tempdir):
    ledger = create_default_ledger(tempdir)
    for d in range(100):
        ledger.add(str(d).encode())
    ledger.stop()
    size_before = ledger.size
    tree_root_hash_before = ledger.tree.root_hash
    tree_size_before = ledger.tree.tree_size
    root_hash_before = ledger.root_hash
    hashes_before = ledger.tree.hashes

    restartedLedger = create_default_ledger(tempdir)
    assert size_before == restartedLedger.size
    assert root_hash_before == restartedLedger.root_hash
    assert hashes_before == restartedLedger.tree.hashes
    assert tree_root_hash_before == restartedLedger.tree.root_hash
    assert tree_size_before == restartedLedger.tree.tree_size


def test_recover_ledger_new_fields_to_txns_added(tempdir):
    fhs = FileHashStore(tempdir)
    tree = CompactMerkleTree(hashStore=fhs)
    ledger = Ledger(tree=tree, dataDir=tempdir, serializer=CompactSerializer(orderedFields))
    for d in range(10):
        ledger.add({"identifier": "i{}".format(d), "reqId": d, "op": "operation"})
    updatedTree = ledger.tree
    ledger.stop()

    newOrderedFields = OrderedDict([
        ("identifier", (str, str)),
        ("reqId", (str, int)),
        ("op", (str, str)),
        ("newField", (str, str))
    ])
    newLedgerSerializer = CompactSerializer(newOrderedFields)

    tree = CompactMerkleTree(hashStore=fhs)
    restartedLedger = Ledger(tree=tree, dataDir=tempdir, serializer=newLedgerSerializer)
    assert restartedLedger.size == ledger.size
    assert restartedLedger.root_hash == ledger.root_hash
    assert restartedLedger.tree.hashes == updatedTree.hashes
    assert restartedLedger.tree.root_hash == updatedTree.root_hash


def test_consistency_verification_on_startup_case_1(tempdir):
    """
    One more node was added to nodes file
    """
    ledger = create_default_ledger(tempdir)
    tranzNum = 10
    for d in range(tranzNum):
        ledger.add(str(d).encode())
    ledger.stop()

    # Writing one more node without adding of it to leaf and transaction logs
    badNode = (None, None, ('X' * 32))
    ledger.tree.hashStore.writeNode(badNode)

    with pytest.raises(ConsistencyVerificationFailed):
        tree = CompactMerkleTree(hashStore=ledger.tree.hashStore)
        ledger = NoTransactionRecoveryLedger(tree=tree, dataDir=tempdir)
        ledger.recoverTreeFromHashStore()
    ledger.stop()


def test_consistency_verification_on_startup_case_2(tempdir):
    """
    One more transaction added to transactions file
    """
    ledger = create_default_ledger(tempdir)
    tranzNum = 10
    for d in range(tranzNum):
        ledger.add(str(d).encode())

    # Adding one more entry to transaction log without adding it to merkle tree
    badData = 'X' * 32
    value = ledger.leafSerializer.serialize(badData, toBytes=False)
    key = str(tranzNum + 1)
    ledger._transactionLog.put(key=key, value=value)

    ledger.stop()

    with pytest.raises(ConsistencyVerificationFailed):
        tree = CompactMerkleTree(hashStore=ledger.tree.hashStore)
        ledger = NoTransactionRecoveryLedger(tree=tree, dataDir=tempdir)
        ledger.recoverTreeFromHashStore()
    ledger.stop()


def test_start_ledger_without_new_line_appended_to_last_record(tempdir, serializer):
    if isinstance(serializer, MsgPackSerializer):
        # MsgPack is a binary one, not compatible with TextFileStorage
        return

    store = TextFileStore(tempdir,
                          'transactions',
                          isLineNoKey=True,
                          storeContentHash=False,
                          ensureDurability=False)
    ledger = Ledger(CompactMerkleTree(hashStore=FileHashStore(dataDir=tempdir)),
                    dataDir=tempdir, serializer=serializer,
                    transactionLogStore=store)

    txnStr = '{"data":{"alias":"Node1","client_ip":"127.0.0.1","client_port":9702,"node_ip":"127.0.0.1",' \
             '"node_port":9701,"services":["VALIDATOR"]},"dest":"Gw6pDLhcBcoQesN72qfotTgFa7cbuqZpkX3Xo6pLhPhv",' \
             '"identifier":"FYmoFw55GeQH7SRFa37dkx1d2dZ3zUF8ckg7wmL7ofN4",' \
             '"txnId":"fea82e10e894419fe2bea7d96296a6d46f50f93f9eeda954ec461b2ed2950b62","type":"0"}'
    lineSep = ledger._transactionLog.lineSep
    lineSep = lineSep if isinstance(lineSep, bytes) else lineSep.encode()
    ledger.start()
    ledger._transactionLog.put(None, txnStr)
    ledger._transactionLog.put(None, txnStr)
    ledger._transactionLog.db_file.write(txnStr)  # here, we just added data without adding new line at the end
    size1 = ledger._transactionLog.size
    assert size1 == 3
    ledger.stop()
    newLineCounts = open(ledger._transactionLog.db_path, 'rb').read().count(lineSep) + 1
    assert newLineCounts == 3

    # now start ledger, and it should add the missing new line char at the end of the file, so
    # if next record gets written, it will be still in proper format and won't break anything.
    ledger.start()
    size2 = ledger._transactionLog.size
    assert size2 == size1
    newLineCountsAferLedgerStart = open(ledger._transactionLog.db_path, 'rb').read().count(lineSep) + 1
    assert newLineCountsAferLedgerStart == 4
    ledger._transactionLog.put(None, txnStr)
    assert ledger._transactionLog.size == 4


def test_add_get_txns(ledger_no_genesis):
    ledger = ledger_no_genesis
    txns = []
    hashes = [hexlify(h).decode() for h in generateHashes(40)]
    for i in range(20):
        txns.append({
            'identifier': hashes.pop(),
            'reqId': i,
            'op': hashes.pop()
        })

    for txn in txns:
        ledger.add(txn)

    check_ledger_generator(ledger)

    for s, t in ledger.getAllTxn(frm=1, to=20):
        assert txns[s-1] == t

    for s, t in ledger.getAllTxn(frm=3, to=8):
        assert txns[s-1] == t

    for s, t in ledger.getAllTxn(frm=5, to=17):
        assert txns[s-1] == t

    for s, t in ledger.getAllTxn(frm=6, to=10):
        assert txns[s-1] == t

    for s, t in ledger.getAllTxn(frm=3, to=3):
        assert txns[s-1] == t

    for s, t in ledger.getAllTxn(frm=3):
        assert txns[s-1] == t

    for s, t in ledger.getAllTxn(to=10):
        assert txns[s-1] == t

    for s, t in ledger.getAllTxn():
        assert txns[s-1] == t

    # with pytest.raises(AssertionError):
    #     list(ledger.getAllTxn(frm=3, to=1))

    for frm, to in [(i, j) for i, j in itertools.permutations(range(1, 21),
                                                              2) if i <= j]:
        for s, t in ledger.getAllTxn(frm=frm, to=to):
            assert txns[s-1] == t
