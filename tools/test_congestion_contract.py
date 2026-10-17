from grade_congestion_matrix import evaluate


def fixture(density=120, collisions=None):
    return {'case':'gridlock_request','samples':[{'t':t,'arterial':{'densityVehPerLaneKm':density}} for t in range(301)],
            'collisionRecords':collisions or [],'invalidRouteIDsSampled':[],
            'results':{'teleports':0},'nativeSummary':{'discarded':'0'}}


def test_sparse_stopped_network_cannot_be_qualified_as_gridlock():
    report=fixture(density=12)
    result=evaluate(report)
    assert result['status']=='REJECT'
    assert any('only 0/300' in failure for failure in result['failedNecessaryGates'])


def test_dense_but_colliding_traffic_is_rejected():
    assert evaluate(fixture(collisions=[{'type':'junction'}]))['status']=='REJECT'


def test_high_density_alone_does_not_prove_gridlock():
    result=evaluate(fixture())
    assert result['status']=='NOT YET QUALIFIED'
    assert 'causal receiving-lane blockage during legal green' in result['pendingGates']


def test_dropped_observation_samples_do_not_pass():
    report=fixture()
    report['samples'].pop(60)
    assert evaluate(report)['status']=='REJECT'
