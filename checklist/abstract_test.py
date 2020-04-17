from abc import ABC, abstractmethod
import dill
from munch import Munch
import numpy as np
import inspect
from .expect import iter_with_optional, Expect

from .viewer.test_summarizer import TestSummarizer

def load_test(file):
    dill._dill._reverse_typemap['ClassType'] = type
    return dill.load(open(file, 'rb'))

def read_pred_file(path, file_format=None, format_fn=None, ignore_header=False):
    f = open(path, 'r')
    if ignore_header:
        f.readline()
    preds = []
    confs = []
    if file_format == 'pred_only':
        format_fn = lambda x: (int(x), 1) if x.isdigit() else (x, 1)
    if file_format == 'pred_and_conf':
        def formatz(x):
            pred, conf = x.split()
            if pred.isdigit():
                pred = int(pred)
            return pred, float(conf)
        format_fn = formatz
    if file_format == 'pred_and_softmax':
        def formatz(x):
            allz = x.split()
            pred = allz[0]
            confs = np.array([float(x) for x in allz[1:]])
            if pred.isdigit():
                pred = int(pred)
            return pred, confs
        format_fn = formatz
    elif file_format is None:
        pass
    else:
        raise(Exception('file_format %s not suported. Accepted values are pred_only, pred_and_conf' % file_format))
    for l in f:
        l = l.strip('\n')
        p, c = format_fn(l)
        preds.append(p)
        confs.append(c)
    return preds, confs

class AbstractTest(ABC):
    def __init__(self, data, expect, labels=None, meta=None, agg_fn='all',
                 templates=None, print_first=None, name=None, capability=None,
                 description=None):
        self.data = data
        self.expect = expect
        self.labels = labels
        self.meta = meta
        self.agg_fn = agg_fn
        self.templates = templates
        self.print_first = print_first
        self.run_idxs = None
        self.name = name
        self.capability = capability
        self.description = description
    def save(self, file):
        dill.dump(self, open(file, 'wb'), recurse=True)

    @staticmethod
    def from_file(file):
        return load_test(file)
        # test, expect = load_test(file)
        # test.expect = dill.loads(expect)
        # return test

    def _extract_examples_per_testcase(
        self, xs, preds, confs, expect_results, labels, meta, nsamples, only_include_fail=True):
        iters = list(iter_with_optional(xs, preds, confs, labels, meta))
        idxs = [0] if self.print_first else []
        idxs = [i for i in np.argsort(expect_results) if not only_include_fail or expect_results[i] <= 0]
        if self.print_first:
            if 0 in idxs:
                idxs.remove(0)
            idxs.insert(0, 0)
        idxs = idxs[:nsamples]
        iters = [iters[i] for i in idxs]
        return idxs, iters, [expect_results[i] for i in idxs]

    def print(self, xs, preds, confs, expect_results, labels=None, meta=None, format_example_fn=None, nsamples=3):
        idxs, iters, _ = self._extract_examples_per_testcase(
            xs, preds, confs, expect_results, labels, meta, nsamples, only_include_fail=True)

        for x, pred, conf, label, meta in iters:
            print(format_example_fn(x, pred, conf, label, meta))
        if type(preds) in [np.array, np.ndarray, list] and len(preds) > 1:
            print()
        print('----')

    def check_results(self):
        if not hasattr(self, 'results') or not self.results:
            raise(Exception('No results. Run run() first'))

    def set_expect(self, expect):
        self.expect = expect
        self.update_expect()

    def update_expect(self):
        self._check_results()
        self.results.expect_results = self.expect(self)
        self.results.passed = Expect.aggregate(self.results.expect_results, self.agg_fn)

    def example_list_and_indices(self, n=None, seed=None):
        if seed is not None:
            np.random.seed(seed)
        self.run_idxs = None
        idxs = list(range(len(self.data)))
        if n is not None:
            idxs = np.random.choice(idxs, min(n, len(idxs)))
            self.run_idxs = idxs
        if type(self.data[0]) in [list, np.array]:
            all = [(i, y) for i in idxs for y in self.data[i]]
            result_indexes, examples = map(list, list(zip(*all)))
        else:
            examples = [self.data[i] for i in idxs]
            result_indexes = idxs# list(range(len(self.data)))
        self.result_indexes = result_indexes
        return examples, result_indexes

    # def example_indices(self):
    #     if type(self.data[0]) in [list, np.array]:
    #         all = [(i, '') for i, x in enumerate(self.data) for y in x]
    #         result_indexes, examples = map(list, list(zip(*all)))
    #         return result_indexes
    #     else:
    #         return list(range(len(self.data)))

    def update_results_from_preds(self, preds, confs):
        # result_indexes = self.example_indices()
        result_indexes = self.result_indexes
        if type(self.data[0]) == list:
            self.results.preds = [[] for _ in self.data]
            self.results.confs  = [[] for _ in self.data]
            for i, p, c in zip(result_indexes, preds, confs):
                self.results.preds[i].append(p)
                self.results.confs[i].append(c)
            for i in range(len(self.results.preds)):
                self.results.preds[i] = np.array(self.results.preds[i])
                self.results.confs[i] = np.array(self.results.confs[i])
        else:
            self.results.preds = [None for _ in self.data]
            self.results.confs = [None for _ in self.data]
            for i, p, c in zip(result_indexes, preds, confs):
                self.results.preds[i] = p
                self.results.confs[i] = c

    def to_raw_examples(self, file_format=None, format_fn=None, n=None, seed=None):
        # file_format can be jsonl, TODO
        # format_fn takes an example and outputs a line in the file
        if file_format == 'jsonl':
            import json
            format_fn = lambda x: json.dumps(x)
        else:
            if format_fn is None:
                format_fn = lambda x: str(x).replace('\n', ' ')
        examples, indices = self.example_list_and_indices(n, seed=seed)
        examples = [format_fn(x) for x in examples]
        return examples

    def to_raw_file(self, path, file_format=None, format_fn=str, header=None, n=None, seed=None):
        # file_format can be jsonl, TODO
        # format_fn takes an example and outputs a line in the file
        ret = ''
        if header is not None:
            ret += header.strip('\n') + '\n'
        examples = self.to_raw_examples(file_format=file_format, format_fn=format_fn, n=n, seed=seed)
        ret += '\n'.join(examples)
        f = open(path, 'w')
        f.write(ret)
        f.close()

    def _results_exist(self):
        return hasattr(self, 'results') and self.results

    def _check_results(self):
        if not self._results_exist():
            raise(Exception('No results. Run run() first'))

    def _check_create_results(self, overwrite, check_only=False):
        if self._results_exist() and not overwrite:
            raise(Exception('Results exist. To overwrite, set overwrite=True'))
        if not check_only:
            self.results = Munch()

    def run_from_preds_confs(self, preds, confs, overwrite=False):
        self._check_create_results(overwrite)
        self.update_results_from_preds(preds, confs)
        self.update_expect()

    def run_from_file(self, path, file_format=None, format_fn=None, ignore_header=False, overwrite=False):
        # file_format can be 'pred_only' (only preds, conf=1), TODO
        # Format_fn takes a line in the file and outputs (pred, conf)
        # Checking just to avoid reading the file in vain
        self._check_create_results(overwrite, check_only=True)
        preds, confs = read_pred_file(path, file_format=file_format,
                                 format_fn=format_fn,
                                 ignore_header=ignore_header)
        self.run_from_preds_confs(preds, confs, overwrite=overwrite)



    def run(self, predict_and_confidence_fn, overwrite=False, verbose=True, n=None, seed=None):
        # Checking just to avoid predicting in vain, will be created in run_from_preds_confs
        self._check_create_results(overwrite, check_only=True)
        examples, result_indexes = self.example_list_and_indices(n, seed=seed)

        if verbose:
            print('Predicting %d examples' % len(examples))
        preds, confs = predict_and_confidence_fn(examples)
        self.run_from_preds_confs(preds, confs, overwrite=overwrite)

    def fail_idxs(self):
        self._check_results()
        return np.where(self.results.passed == False)[0]

    def filtered_idxs(self):
        self._check_results()
        return np.where(self.results.passed == None)[0]

    def print_stats(self):
        self._check_results()
        n_run = n = len(self.data)
        if self.run_idxs is not None:
            n_run = len(self.run_idxs)
        fails = self.fail_idxs().shape[0]
        filtered = self.filtered_idxs().shape[0]
        nonfiltered = n_run - filtered
        print('Test cases:      %d' % n)
        if n_run != n:
            print('Test cases run:  %d' % n_run)
        if filtered:
            print('After filtering: %d (%.1f%%)' % (nonfiltered, 100 * nonfiltered / n_run))
        if nonfiltered != 0:
            print('Fails (rate):    %d (%.1f%%)' % (fails, 100 * fails / nonfiltered))

    def label_meta(self, i):
        if self.labels is None:
            label = None
        else:
            label = self.labels if type(self.labels) not in [list, np.array] else self.labels[i]
        if self.meta is None:
            meta = None
        else:
            meta = self.meta if type(self.meta) not in [list, np.array] else self.meta[i]
        return label, meta

    def summary(self, n=3, print_fn=None, format_example_fn=None, n_per_testcase=3):
        # print_fn_fn takes (xs, preds, confs, expect_results, labels=None, meta=None)
        # format_example_fn takes (x, pred, conf, label=None, meta=None)
        # i.e. it prints a single test case
        self.print_stats()
        if not n:
            return
        if print_fn is None:
            print_fn = self.print
        def default_format_example(x, pred, conf, *args, **kwargs):
            softmax = type(conf) in [np.array, np.ndarray]
            binary = False
            if softmax:
                if conf.shape[0] == 2:
                    conf = conf[1]
                    return '%.1f %s' % (conf, str(x))
                elif conf.shape[0] <= 4:
                    confs = ' '.join(['%.1f' % c for c in conf])
                    return '%s %s' % (confs, str(x))

                else:
                    conf = conf[pred]
                    return '%s (%.1f) %s' % (pred, conf, str(x))

        if format_example_fn is None:
            format_example_fn = default_format_example
        fails = self.fail_idxs()
        if fails.shape[0] == 0:
            return
        print()
        print('Example fails:')
        fails = np.random.choice(fails, min(fails.shape[0], n), replace=False)
        for f in fails:
            d_idx = f if self.run_idxs is None else self.run_idxs[f]
            # should be format_fn
            label, meta = self.label_meta(d_idx)
            # print(label, meta)
            print_fn(self.data[d_idx], self.results.preds[d_idx],
                     self.results.confs[d_idx], self.results.expect_results[f],
                     label, meta, format_example_fn, nsamples=n_per_testcase)

    def _form_examples_per_testcase_for_viz(
        self, xs, preds, confs, expect_results, labels=None, meta=None, nsamples=3):
        idxs, iters, expect_results_sample = self._extract_examples_per_testcase(
            xs, preds, confs, expect_results, labels, meta, nsamples, only_include_fail=False)
        if not iters:
            return []
        start_idx = 1 if self.print_first else 0
        if self.print_first:
            base = iters[0]
            try:
                conf = base[2][base[1]]
            except:
                conf = None
            old_example = {"text": base[0], "pred": str(base[1]), "conf": conf}
        else:
            old_example = None

        examples = []
        for idx, e in enumerate(iters[start_idx:]):
            try:
                conf = e[2][e[1]]
            except:
                conf = None
            example = {
                "new": {"text": e[0], "pred": str(e[1]), "conf": conf},
                "old": old_example,
                "label": e[3],
                "succeed": int(expect_results_sample[start_idx:][idx] > 0)
            }
            examples.append(example)
        return examples

    def _form_test_info(self, name=None, description=None, capability=None):
        n = len(self.data)
        fails = self.fail_idxs().shape[0]
        filtered = self.filtered_idxs().shape[0]
        return {
            "name": name if name else self.name,
            "description": description if description else self.description,
            "capability": capability if capability else self.capability,
            "type": self.__class__.__name__.lower(),
            "tags": [],
            "stats": {
                "nfailed": fails,
                "npassed": n - filtered - fails,
                "nfiltered": filtered
            }
        }

    def visual_summary(self, name=None, description=None, capability=None, n_per_testcase=3):
        self.check_results()
        # get the test meta
        test_info = self._form_test_info(name, description, capability)
        testcases = []
        nonfiltered_idxs = np.where(self.results.passed != None)[0]
        for f in nonfiltered_idxs:
            # should be format_fn
            label, meta = self.label_meta(f)
            # print(label, meta)
            succeed = self.results.passed[f]
            if succeed is not None:
                examples = self._form_examples_per_testcase_for_viz(
                    self.data[f], self.results.preds[f],
                    self.results.confs[f], self.results.expect_results[f],
                    label, meta, nsamples=n_per_testcase)
            else:
                examples = []
            if examples:
                testcases.append({
                    "examples": examples,
                    "succeed": int(succeed),
                    "tags": []
                })
        return TestSummarizer(test_info, testcases)
