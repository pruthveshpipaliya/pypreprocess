import os
import glob
from configobj import ConfigObj
import numpy as np
from subject_data import SubjectData


def _del_nones_from_dict(some_dict):
    if isinstance(some_dict, dict):
        for k, v in some_dict.iteritems():
            if v is None:
                del some_dict[k]
            else:
                _del_nones_from_dict(v)

    return some_dict


def _parse_job(jobfile):
    assert os.path.isfile(jobfile)

    def sanitize(section, key):
        val = section[key]

        if key == "slice_order":
            if isinstance(val, basestring):
                return

        if isinstance(val, basestring):
            if val.lower() in ["true", "yes"]:
                val = True
            elif val.lower() in ["false", "no"]:
                val = False
            elif key == "slice_order":
                val = val.lower()
            elif val.lower() in ["none", "auto"]:
                val = None

        if key in ["TR", "nslices", "refslice", "nsubjects", "nsessions",
                   "n_jobs"]:
            if not val is None:
                val = eval(val)

        if key in ["fwhm", "anat_voxel_sizes", "func_voxel_sizes",
                   "slice_order"]:
            dtype = np.int if key == "slice_order" else np.float
            val = ",".join(val).replace("[", "")
            val = val.replace("]", "")
            val = list(np.fromstring(val, sep=",", dtype=dtype))
            if len(val) == 1:
                val = val[0]

        section[key] = val

    cobj = ConfigObj(jobfile)
    cobj.walk(sanitize, call_on_sections=True)

    return cobj['config']


def _generate_preproc_pipeline(jobfile):
    """
    Generate pipeline (i.e subject factor + preproc params) from
    config file.

    """

    # read config file
    jobfile = os.path.abspath(jobfile)
    options = _parse_job(jobfile)
    options = _del_nones_from_dict(options)

    # generate subject conf
    subjects = []
    acquisition_dir = os.path.abspath(os.path.dirname(jobfile))
    old_cwd = os.getcwd()
    os.chdir(acquisition_dir)

    # output dir
    output_dir = options["output_dir"]
    if output_dir.startswith("./"):
        output_dir = output_dir[2:]
    elif output_dir.startswith("."):
        output_dir = output_dir[1:]
    output_dir = os.path.abspath(output_dir)

    # how many subjects ?
    subject_count = 0
    nsubjects = options.get('nsubjects', np.inf)
    exclude_these_subject_ids = options.get(
        'exclude_these_subject_ids', [])
    include_only_these_subject_ids = options.get(
        'include_only_these_subject_ids', [])

    def _ignore_subject(subject_id):
        """
        Ignore given subject_id ?

        """

        if subject_id in exclude_these_subject_ids:
            return True
        elif len(include_only_these_subject_ids
               ) and not subject_id in include_only_these_subject_ids:
            return True
        else:
            return False

    # subject data factory
    func_dir = options.get('func_dir', '')
    nsessions = options.get('nsessions', 1)
    subject_dir_wildcard = os.path.join(acquisition_dir,
                                        options.get("subject_dir_wildcard",
                                                    "*"))
    for subject_data_dir in sorted(glob.glob(subject_dir_wildcard)):
        if subject_count == nsubjects:
            break

        subject_id = os.path.basename(subject_data_dir)
        if _ignore_subject(subject_id):
            continue
        else:
            subject_count += 1

        # grab functional data
        sess_dir_wildcard = options.get("session_dir_wildcard", "")
        if sess_dir_wildcard in [".", None]:
            sess_dir_wildcard = ""
        func = []
        sess_dir_wildcard = os.path.join(subject_data_dir, func_dir,
                                         sess_dir_wildcard)
        for sess_dir in sorted(glob.glob(sess_dir_wildcard)):
            sess_func_wild_card = os.path.join(sess_dir, options.get(
                    "func_basename_wildcard", "*"))
            sess_func = sorted(glob.glob(sess_func_wild_card))
            assert len(sess_func), ("subject %s: No func images found for"
                                    " wildcard %s" % (
                    subject_id, sess_func_wild_card))
            func.append(sess_func)

        assert len(func), ("subject %s: No func images found for "
                           "wildcard: %s !") % (subject_id, sess_dir_wildcard)
        assert len(func) == nsessions, ("subject %s: Expecting func data "
                                        "for %i sessions; got %i" % (
                subject_id, nsessions, len(func)))

        # grab anat
        anat = None
        if not options.get("anat_basename", None) is None:
            anat_dir = options.get("anat_dir", None)
            if anat_dir in [".", None]:
                anat_dir = ""
            anat = glob.glob(os.path.join(subject_data_dir,
                                          anat_dir,
                                          options["anat_basename"]
                                          ))
            assert len(anat) > 0, "Anatomical image not found!"
            anat = anat[0]

        # make subject data
        subject_data = SubjectData(subject_id=subject_id, func=func, anat=anat,
                                   output_dir=os.path.join(output_dir,
                                                           subject_id))

        subjects.append(subject_data)

    assert subjects, "No subject directories found for wildcard: %s" % (
        subject_dir_wildcard)

    # preproc parameters
    preproc_params = {"report": options.get("report", True),
                      "output_dir": output_dir,
                      "dataset_id": options.get("dataset_id", acquisition_dir),
                      "n_jobs": options.get("n_jobs", None),
                      "caching": options.get("caching", True),
                      "cv_tc": options.get("cv_tc", True)}

    # delete orientation meta-data ?
    preproc_params['deleteorient'] = options.get(
        "deleteorient", False)

    # configure slice-timing correction node
    preproc_params["slice_timing"] = not options.get(
        "disable_slice_timing", False)
    if not preproc_params["slice_timing"]:
        preproc_params.update(dict((k, options.get(k, None))
                                   for k in ["TR", "TA", "slice_order",
                                             "interleaved"]))

    # configure motion correction node
    preproc_params["realign"] = not options.get("disable_realign", False)
    if preproc_params["realign"]:
        preproc_params['realign_reslice'] = options.get("reslice_realign",
                                                        False)
        preproc_params['register_to_mean'] = options.get("register_to_mean",
                                                         True)

    # configure coregistration node
    preproc_params["coregister"] = not options.get("disable_coregister",
                                                   False)
    if preproc_params["coregister"]:
        preproc_params['coregister_reslice'] = options["coregister_reslice"]
        preproc_params['coreg_anat_to_func'] = not options.get(
            "coreg_func_to_anat", True)

    # configure tissue segmentation node
    preproc_params["segment"] = not options.get("disable_segment", False)
    if preproc_params["segment"]:
        pass  # XXX pending code...

    # configure normalization node
    preproc_params["normalize"] = not options.get(
        "disable_normalize", False)
    if preproc_params["normalize"]:
        preproc_params['func_write_voxel_sizes'] = options.get(
            "func_voxel_sizes", [3, 3, 3])
        preproc_params['anat_write_voxel_sizes'] = options.get(
            "anat_voxel_sizes", [1, 1, 1])
        preproc_params['dartel'] = options.get("dartel", False)

    # configure smoothing node
    preproc_params["fwhm"] = options.get("fwhm", 0.)

    os.chdir(old_cwd)

    return subjects, preproc_params

if __name__ == '__main__':
    from pypreprocess.reporting.base_reporter import dict_to_html_ul
    print dict_to_html_ul(_parse_job("job.conf"))
