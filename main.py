#!/usr/bin/env python3
import argparse
import config
import os
from subprocess import check_call
import pathlib
from send2trash import send2trash
from github import Github, GithubException
from getpass import getpass
import datetime

ROOT = os.path.abspath(os.path.dirname(__file__))
MIGRATION_DATA_DIR = os.path.join(ROOT, "migration_data")


def bitbucket_repo_url(repo):
    return "ssh://hg@bitbucket.org/" + repo


def github_repo_url(repo):
    return "git@github.com:" + repo + ".git"


def execute(cmd, *args, **kwargs):
    check_call(cmd, *args, shell=True, **kwargs)


def step(msg):
    now = datetime.datetime.now()
    time = now.strftime("%Y-%m-%d %H:%M:%S")
    print("\n[{}] === {}...".format(time, msg))


def is_github_repo_empty(github, grepo):
    repo = github.get_repo(grepo)
    try:
        repo.get_contents("/")
        return True
    except GithubException.GithubException as e:
        print("> " + e.args[1]["message"])
        return e.args[1]["message"] == "This repository is empty."


def create_parser():
    parser = argparse.ArgumentParser(
        prog="migrate",
        description="Migrate mercurial repositories from Bitbucket to Github"
    )
    parser.add_argument(
        "-t", "--github-access-token",
        help="Github Access Token",
        required=True
    )
    parser.add_argument(
        "--hg-fast-export-path",
        help="Path to the hg-fast-export.sh script",
        required=True
    )
    parser.add_argument(
        "--bitbucket-username",
        help="Bitbucket username",
        required=True
    )
    parser.add_argument(
        "--bitbucket-password",
        help="Bitbucket password"
    )
    parser.add_argument(
        "bitbucket_repositories",
        nargs="+",
        help="List of the Bitbucket repositories that should migrate to Github"
    )
    return parser


def main():
    parser = create_parser()
    args = parser.parse_args()

    repositories_to_migrate = {
        brepo: config.KNOWN_REPO_MAPPING[brepo]
        for brepo in args.bitbucket_repositories
    }

    github = Github(args.github_access_token, timeout=30, retry=3, per_page=100)

    if args.bitbucket_password is None:
        args.bitbucket_password = getpass(prompt="Password of Bitbucket's user '{}': ".format(args.bitbucket_username))

    for brepo, grepo in repositories_to_migrate.items():
        step("Cloning bitbucket repository '{}' to local mercurial repository".format(brepo))
        hg_folder = os.path.join(MIGRATION_DATA_DIR, "bitbucket", brepo)
        brepo_url = bitbucket_repo_url(brepo)
        if os.path.isdir(hg_folder):
            send2trash(hg_folder)
        pathlib.Path(hg_folder).mkdir(parents=True, exist_ok=True)
        execute("hg clone " + brepo_url + " " + hg_folder, cwd=MIGRATION_DATA_DIR)

    for brepo, grepo in repositories_to_migrate.items():
        step("Importing forks of bitbucket repository '{}' into local mercurial repository".format(brepo))
        execute("./import-forks.py --repo {} --bitbucket-repository {} --bitbucket-username {} --bitbucket-password {}".format(
            hg_folder,
            brepo,
            args.bitbucket_username,
            args.bitbucket_password
        ), cwd=ROOT)

    for brepo, grepo in repositories_to_migrate.items():
        step("Preparing local git repository for '{}'".format(grepo))
        git_folder = os.path.join(MIGRATION_DATA_DIR, "github", grepo)
        if os.path.isdir(git_folder):
            send2trash(git_folder)
        pathlib.Path(git_folder).mkdir(parents=True, exist_ok=True)
        execute("git init", cwd=git_folder)
        execute("git config core.ignoreCase false", cwd=git_folder)

    for brepo, grepo in repositories_to_migrate.items():
        step("Converting local mercurial repository of '{}' to git".format(brepo))
        execute("{} -r {} --hg-hash".format(
            args.hg_fast_export_path,
            hg_folder
        ), cwd=git_folder)

    for brepo, grepo in repositories_to_migrate.items():
        step("Pushing local git repository to github repository '{}'".format(grepo))
        assert is_github_repo_empty(github, grepo), "Github repository '{}' is non-empty. Please delete and recreate it.".format(grepo)
        execute("git remote add origin {}".format(
            github_repo_url(grepo)
        ), cwd=git_folder)
        execute("git push --set-upstream origin master", cwd=git_folder)
        execute("git push --all origin", cwd=git_folder)

    for brepo, grepo in repositories_to_migrate.items():
        step("Mapping local mercurial commit hashes of '{}' to git".format(brepo))
        execute("./hg-git-commit-map.py --repo {} --bitbucket-repository {}".format(
            git_folder,
            brepo
        ), cwd=ROOT)

    for brepo, grepo in repositories_to_migrate.items():
        step("Migrate isues and pull requests of bitbucket repository '{}' to github".format(brepo))
        execute("./migrate-discussions.py --github-access-token {} --bitbucket-repository {} --github-repository {}".format(
            args.github_access_token,
            brepo,
            grepo
        ), cwd=ROOT)


if __name__ == "__main__":
    main()
